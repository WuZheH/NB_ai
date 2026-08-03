from __future__ import annotations

import json
import sqlite3
from typing import Any, Mapping

from app.services.note_object_alignment_service import build_note_object_alignment_dry_run


REVIEWED_OBJECT_STATUSES = {"accepted", "approved", "edited", "reviewed", "committed"}


def build_object_import_review_pack(
    conn: sqlite3.Connection,
    note: Mapping[str, Any],
    *,
    alignment_result: Mapping[str, Any] | None = None,
    document_id: int | None = None,
    chapter_id: int | None = None,
) -> dict[str, Any]:
    conn.row_factory = sqlite3.Row
    alignment = dict(
        alignment_result
        or build_note_object_alignment_dry_run(
            conn,
            note,
            document_id=document_id,
            chapter_id=chapter_id,
        )
    )
    object_ids = list((alignment.get("object_alignment") or {}).get("matched_object_ids") or [])
    document_alignment = alignment.get("document_alignment") or {}
    matched_chunk_ids = list(document_alignment.get("matched_chunk_ids") or [])
    terms = list((alignment.get("object_alignment") or {}).get("candidate_object_terms") or [])
    candidates = [
        _review_candidate(row, note=note, matched_chunk_ids=matched_chunk_ids, terms=terms)
        for row in _load_object_candidates(conn, object_ids)
    ]
    reviewed = [item for item in candidates if item["review_state"] == "reviewed_existing"]
    missing = []
    if not candidates:
        missing.append("object_candidates")
    if not reviewed:
        missing.append("reviewed_object_candidate")
    pack = {
        "schema_version": "object_import_review_b_v1",
        "status": "review_ready" if candidates else "blocked",
        "source_mode": _source_mode(note),
        "source_context": {
            "note_identity": alignment.get("note_identity") or {},
            "matched_document_id": document_alignment.get("matched_document_id"),
            "matched_chapter_id": document_alignment.get("matched_chapter_id"),
            "matched_chunk_ids": matched_chunk_ids,
            "primary_user_note": {
                "note_text": note.get("note_text") or "",
                "role": "primary_source",
            },
            "primary_source_excerpt": {
                "selected_text": note.get("selected_text") or "",
                "page_label": note.get("page_label") or "",
                "role": "primary_source",
            },
        },
        "candidate_reviews": candidates,
        "reviewed_object_ids_for_source_pack": [item["object_id"] for item in reviewed],
        "review_policy": {
            "human_review_required": True,
            "auto_approval_allowed": False,
            "approved_or_edited_only_for_source_pack": True,
            "preserve_note_and_source_provenance": True,
            "object_layer_is_semantic_support": True,
        },
        "readiness": {
            "ready_for_object_review": bool(candidates),
            "ready_for_mechanism_source_pack": bool(reviewed),
            "missing": missing,
        },
        "alignment_result": alignment,
    }
    return {
        "object_import_review_pack": pack,
        **_safety_flags(),
    }


def _load_object_candidates(conn: sqlite3.Connection, object_ids: list[int]) -> list[dict[str, Any]]:
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
    return [dict(row) for row in rows]


def _review_candidate(
    row: Mapping[str, Any],
    *,
    note: Mapping[str, Any],
    matched_chunk_ids: list[int],
    terms: list[str],
) -> dict[str, Any]:
    object_chunks = _json_int_list(row.get("mapped_chunk_ids_json"))
    evidence_refs = _json_list(row.get("evidence_refs_json"))
    chunk_overlap = sorted(set(object_chunks) & set(matched_chunk_ids))
    object_text = f"{row.get('object_name') or ''} {row.get('object_type') or ''}".casefold()
    term_overlap = [term for term in terms if len(term) >= 3 and term.casefold() in object_text]
    reviewed = _is_reviewed(row)
    return {
        "object_id": row.get("object_id"),
        "object_name": row.get("object_name") or "",
        "object_type": row.get("object_type") or "",
        "review_status": row.get("review_status") or "",
        "status": row.get("status") or "",
        "review_state": "reviewed_existing" if reviewed else "needs_human_review",
        "suggested_action": "use_reviewed_object" if reviewed else "review_before_source_pack",
        "matched_by": _matched_by(chunk_overlap, term_overlap),
        "matched_chunk_ids": chunk_overlap,
        "candidate_object_terms": term_overlap,
        "source_roles": _source_roles(note),
        "evidence_refs": evidence_refs,
        "risk": "" if reviewed else "unreviewed_object_candidate_blocked_from_mechanism_source_pack",
    }


def _is_reviewed(row: Mapping[str, Any]) -> bool:
    return str(row.get("review_status") or "").casefold() in REVIEWED_OBJECT_STATUSES or str(
        row.get("status") or ""
    ).casefold() in REVIEWED_OBJECT_STATUSES


def _source_mode(note: Mapping[str, Any]) -> str:
    has_note = bool(str(note.get("note_text") or "").strip())
    has_source = bool(str(note.get("selected_text") or "").strip())
    if has_note and has_source:
        return "joint_anchored"
    if has_note:
        return "note_anchored"
    if has_source:
        return "source_anchored"
    return "unknown"


def _source_roles(note: Mapping[str, Any]) -> list[str]:
    roles = []
    if str(note.get("note_text") or "").strip():
        roles.append("primary_user_note")
    if str(note.get("selected_text") or "").strip():
        roles.append("primary_source_excerpt")
    return roles


def _matched_by(chunk_overlap: list[int], term_overlap: list[str]) -> list[str]:
    values = []
    if chunk_overlap:
        values.append("chunk_overlap")
    if term_overlap:
        values.append("term_overlap")
    return values or ["alignment_candidate"]


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


def _json_int_list(value: Any) -> list[int]:
    ids = []
    for item in _json_list(value):
        try:
            integer = int(item)
        except (TypeError, ValueError):
            continue
        if integer not in ids:
            ids.append(integer)
    return ids


def _safety_flags() -> dict[str, bool]:
    return {
        "db_write_performed": False,
        "llm_called": False,
        "relation_generated": False,
        "mechanism_generated": False,
        "zotero_write_performed": False,
        "vector_store_write_performed": False,
    }
