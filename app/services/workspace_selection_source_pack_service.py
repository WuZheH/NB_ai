from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Mapping

from app.core.paths import DEFAULT_DB_PATH
from app.services.mechanism_source_pack_service import (
    REVIEWED_OBJECT_STATUSES,
    build_mechanism_source_pack_dry_run,
)
from app.services.note_object_alignment_service import load_zotero_note
from app.services.unit_note_object_processing_service import connect_readonly


SELECTION_PACK_SCHEMA_VERSION = "workspace_selection_source_pack_v1"


def build_workspace_selection_source_pack_preview(
    *,
    research_db_path: str | Path = DEFAULT_DB_PATH,
    document_id: int,
    chapter_id: int,
    chunk_id: int | None,
    server_note_id: str | None = None,
    client_note_id: str | None = None,
    object_candidate_ids: list[int] | None = None,
    reviewed_object_refs: list[str] | None = None,
) -> dict[str, Any]:
    blockers = _input_blockers(
        document_id=document_id,
        chapter_id=chapter_id,
        chunk_id=chunk_id,
        server_note_id=server_note_id,
        client_note_id=client_note_id,
    )
    if blockers:
        return _blocked(blockers)

    db_path = Path(research_db_path)
    with connect_readonly(db_path) as conn:
        conn.row_factory = sqlite3.Row
        chapter = _chapter_row(conn, document_id=document_id, chapter_id=chapter_id)
        if chapter is None:
            return _blocked(["chapter_not_found_in_document_scope"])

        chunk = _chunk_row(conn, chunk_id=int(chunk_id))
        if chunk is None:
            return _blocked(["selected_chunk_not_found"])
        if int(chunk.get("document_id") or 0) != int(document_id):
            return _blocked(["selected_chunk_document_mismatch"])
        if int(chunk.get("chapter_id") or 0) != int(chapter_id):
            return _blocked(["selected_chunk_chapter_mismatch"])

        note = load_zotero_note(
            conn,
            server_note_id=_text(server_note_id) or None,
            client_note_id=_text(client_note_id) or None,
        )
        if note is None:
            return _blocked(["selected_note_not_found"])
        note_document_id = _optional_int(note.get("matched_document_id"))
        if note_document_id != int(document_id):
            return _blocked(["selected_note_document_mismatch"])
        matched_chunk_ids = _json_int_list(note.get("matched_chunk_ids_json"))
        primary_matched_chunk_id = _optional_int(note.get("matched_chunk_id"))
        if primary_matched_chunk_id is not None and primary_matched_chunk_id not in matched_chunk_ids:
            matched_chunk_ids.insert(0, primary_matched_chunk_id)
        if int(chunk_id) not in matched_chunk_ids:
            return _blocked(["selected_chunk_not_aligned_to_note"])

        explicit_object_ids = _unique_ints(object_candidate_ids or [])
        explicit_reviewed_refs = _unique_texts(reviewed_object_refs or [])
        object_rows, object_blockers = _validated_objects(
            conn,
            object_ids=explicit_object_ids,
            document_id=document_id,
            chunk_id=int(chunk_id),
        )
        reviewed_object_rows, reviewed_object_blockers = _validated_reviewed_objects(
            conn,
            object_refs=explicit_reviewed_refs,
            document_id=document_id,
            chapter_id=chapter_id,
            chunk_id=int(chunk_id),
            selected_note_ids=_unique_texts(
                [note.get("server_note_id"), note.get("client_note_id")]
            ),
        )
        all_object_rows = [*object_rows, *reviewed_object_rows]
        if object_blockers or reviewed_object_blockers:
            return _blocked([*object_blockers, *reviewed_object_blockers])

        normalized_note = dict(note)
        normalized_note["user_tags"] = _json_list(note.get("user_tags_json"))
        alignment = {
            "note_identity": {
                "server_note_id": note.get("server_note_id"),
                "client_note_id": note.get("client_note_id"),
                "zotero_attachment_key": note.get("zotero_attachment_key"),
                "zotero_annotation_key": note.get("zotero_annotation_key"),
            },
            "document_alignment": {
                "matched_document_id": int(document_id),
                "matched_chapter_id": int(chapter_id),
                "matched_chunk_ids": [int(chunk_id)],
                "alignment_confidence": 1.0,
                "alignment_method": "explicit_workspace_selection_exact_scope",
                "warnings": [],
            },
            "object_alignment": {
                "matched_object_ids": [int(row["id"]) for row in object_rows],
                "matched_object_refs": [row["candidate_temp_id"] for row in reviewed_object_rows],
                "alignment_method": (
                    "explicit_workspace_selection_exact_chunk"
                    if all_object_rows
                    else "no_explicit_object_selected"
                ),
                "warnings": [] if all_object_rows else ["linked_objects_not_selected"],
            },
            "readiness": {
                "ready_for_mechanism_source_pack": True,
                "missing": [],
            },
            **_safety_flags(),
        }
        result = build_mechanism_source_pack_dry_run(
            conn,
            normalized_note,
            alignment_result=alignment,
            object_ids=[int(row["id"]) for row in object_rows],
            linked_objects=all_object_rows,
            chunk_id=int(chunk_id),
            document_id=int(document_id),
        )

    pack = result.get("mechanism_source_pack") or {}
    parity_blockers = _parity_blockers(pack)
    if parity_blockers:
        return _blocked(parity_blockers, source_pack_result=result)
    warnings = list(pack.get("warnings") or [])
    if not all_object_rows and "linked_objects_not_selected" not in warnings:
        warnings.append("linked_objects_not_selected")
    pack["warnings"] = warnings
    return {
        "status": "OK",
        "schema_version": SELECTION_PACK_SCHEMA_VERSION,
        "selection_ready": True,
        "selection_scope": {
            "document_id": int(document_id),
            "chapter_id": int(chapter_id),
            "chunk_id": int(chunk_id),
            "server_note_id": note.get("server_note_id"),
            "client_note_id": note.get("client_note_id"),
            "object_candidate_ids": [int(row["id"]) for row in object_rows],
            "scope_binding": "exact_document_chapter_note_chunk",
            "reviewed_object_refs": [
                row["candidate_temp_id"] for row in reviewed_object_rows
            ],
            "linked_object_refs": [
                *[f"object_candidate:{row['id']}" for row in object_rows],
                *[row["object_ref"] for row in reviewed_object_rows],
            ],
        },
        "source_pack_result": result,
        "blockers": [],
        "warnings": warnings,
        **_safety_flags(),
    }


def _input_blockers(
    *,
    document_id: int,
    chapter_id: int,
    chunk_id: int | None,
    server_note_id: str | None,
    client_note_id: str | None,
) -> list[str]:
    blockers: list[str] = []
    if _optional_int(document_id) is None:
        blockers.append("document_id_required")
    if _optional_int(chapter_id) is None:
        blockers.append("chapter_id_required")
    if _optional_int(chunk_id) is None:
        blockers.append("selected_chunk_required")
    if not _text(server_note_id) and not _text(client_note_id):
        blockers.append("selected_note_identity_required")
    return blockers


def _chapter_row(
    conn: sqlite3.Connection,
    *,
    document_id: int,
    chapter_id: int,
) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT id, document_id, title FROM book_chapters WHERE id = ? AND document_id = ?",
        (int(chapter_id), int(document_id)),
    ).fetchone()
    return dict(row) if row else None


def _chunk_row(conn: sqlite3.Connection, *, chunk_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT id, document_id, chapter_id, chunk_text, pdf_page_start
        FROM knowledge_chunks
        WHERE id = ?
        """,
        (int(chunk_id),),
    ).fetchone()
    return dict(row) if row else None


def _validated_objects(
    conn: sqlite3.Connection,
    *,
    object_ids: list[int],
    document_id: int,
    chunk_id: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    if not object_ids:
        return [], []
    placeholders = ", ".join("?" for _ in object_ids)
    rows = conn.execute(
        f"""
        SELECT id, document_id, object_name, object_type, review_status, status,
               mapped_chunk_ids_json, evidence_refs_json
        FROM object_candidates
        WHERE id IN ({placeholders})
        ORDER BY id
        """,
        tuple(object_ids),
    ).fetchall()
    by_id = {int(row["id"]): dict(row) for row in rows}
    blockers: list[str] = []
    ordered_rows: list[dict[str, Any]] = []
    for object_id in object_ids:
        row = by_id.get(int(object_id))
        if row is None:
            blockers.append(f"selected_object_not_found:{object_id}")
            continue
        if _optional_int(row.get("document_id")) != int(document_id):
            blockers.append(f"selected_object_document_mismatch:{object_id}")
            continue
        status = _text(row.get("review_status") or row.get("status")).casefold()
        if status not in REVIEWED_OBJECT_STATUSES:
            blockers.append(f"selected_object_not_reviewed:{object_id}")
            continue
        if int(chunk_id) not in _json_int_list(row.get("mapped_chunk_ids_json")):
            blockers.append(f"selected_object_chunk_mismatch:{object_id}")
            continue
        ordered_rows.append(row)
        row["object_id"] = int(row["id"])
    return ordered_rows, blockers
def _validated_reviewed_objects(
    conn: sqlite3.Connection,
    *,
    object_refs: list[str],
    document_id: int,
    chapter_id: int,
    chunk_id: int,
    selected_note_ids: list[str],
) -> tuple[list[dict[str, Any]], list[str]]:
    if not object_refs:
        return [], []
    if not _table_exists(conn, "object_candidate_human_review_items"):
        return [], [f"selected_reviewed_object_not_found:{ref}" for ref in object_refs]

    placeholders = ", ".join("?" for _ in object_refs)
    rows = conn.execute(
        f"""
        SELECT id, review_item_id, human_review_id, source_draft_review_id,
               source_draft_item_id, candidate_temp_id, document_id, chapter_id,
               action, final_object_name, final_object_type, human_note,
               approved_candidate, source_server_note_ids_json,
               evidence_chunk_ids_json, page_labels_json
        FROM object_candidate_human_review_items
        WHERE candidate_temp_id IN ({placeholders})
        ORDER BY id
        """,
        tuple(object_refs),
    ).fetchall()
    by_ref = {_text(row["candidate_temp_id"]): dict(row) for row in rows}
    blockers: list[str] = []
    ordered_rows: list[dict[str, Any]] = []
    for object_ref in object_refs:
        row = by_ref.get(object_ref)
        if row is None:
            blockers.append(f"selected_reviewed_object_not_found:{object_ref}")
            continue
        if _optional_int(row.get("document_id")) != int(document_id):
            blockers.append(f"selected_reviewed_object_document_mismatch:{object_ref}")
            continue
        if _optional_int(row.get("chapter_id")) != int(chapter_id):
            blockers.append(f"selected_reviewed_object_chapter_mismatch:{object_ref}")
            continue
        if _optional_int(row.get("approved_candidate")) != 1:
            blockers.append(f"selected_reviewed_object_not_approved:{object_ref}")
            continue
        if int(chunk_id) not in _json_int_list(row.get("evidence_chunk_ids_json")):
            blockers.append(f"selected_reviewed_object_chunk_mismatch:{object_ref}")
            continue
        source_note_ids = _unique_texts(_json_list(row.get("source_server_note_ids_json")))
        if not set(source_note_ids).intersection(selected_note_ids):
            blockers.append(f"selected_reviewed_object_note_mismatch:{object_ref}")
            continue
        ordered_rows.append(_normalize_reviewed_object(row))
    return ordered_rows, blockers


def _normalize_reviewed_object(row: Mapping[str, Any]) -> dict[str, Any]:
    object_ref = _text(row.get("candidate_temp_id"))
    return {
        "object_id": object_ref,
        "object_ref": f"reviewed_object:{object_ref}",
        "candidate_temp_id": object_ref,
        "object_source": "object_candidate_human_review_items",
        "document_id": _optional_int(row.get("document_id")),
        "chapter_id": _optional_int(row.get("chapter_id")),
        "object_name": row.get("final_object_name"),
        "object_type": row.get("final_object_type"),
        "review_status": "approved_human_review_read_only",
        "status": row.get("action"),
        "human_note": row.get("human_note"),
        "mapped_chunk_ids_json": row.get("evidence_chunk_ids_json"),
        "evidence_refs_json": row.get("evidence_chunk_ids_json"),
        "source_server_note_ids_json": row.get("source_server_note_ids_json"),
        "page_labels_json": row.get("page_labels_json"),
        "review_item_id": row.get("review_item_id"),
        "human_review_id": row.get("human_review_id"),
        "source_draft_review_id": row.get("source_draft_review_id"),
        "source_draft_item_id": row.get("source_draft_item_id"),
        "semantic_role": "semantic_support",
    }


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone() is not None




def _parity_blockers(pack: Mapping[str, Any]) -> list[str]:
    blockers: list[str] = []
    note = pack.get("primary_user_note") or {}
    excerpt = pack.get("primary_source_excerpt") or {}
    if not _text(note.get("note_text")):
        blockers.append("primary_user_note_text_required")
    if not _text(excerpt.get("selected_text")) and not _text(excerpt.get("chunk_text")):
        blockers.append("primary_source_excerpt_text_required")
    if _text(pack.get("source_mode")) != "joint_led":
        blockers.append("workspace_selection_requires_joint_led_source_mode")
    return blockers


def _blocked(
    blockers: list[str],
    *,
    source_pack_result: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "status": "BLOCKED",
        "schema_version": SELECTION_PACK_SCHEMA_VERSION,
        "selection_ready": False,
        "selection_scope": None,
        "source_pack_result": dict(source_pack_result or {}) or None,
        "blockers": list(dict.fromkeys(blockers)),
        "warnings": [],
        **_safety_flags(),
    }


def _json_list(value: Any) -> list[Any]:
    try:
        parsed = json.loads(str(value or "[]"))
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def _json_int_list(value: Any) -> list[int]:
    return _unique_ints(_json_list(value))


def _unique_ints(values: list[Any]) -> list[int]:
    result: list[int] = []
    for value in values:
        integer = _optional_int(value)
        if integer is not None and integer not in result:
            result.append(integer)
    return result


def _unique_texts(values: list[Any]) -> list[str]:
    result: list[str] = []
    for value in values:
        text = _text(value)
        if text and text not in result:
            result.append(text)
    return result


def _optional_int(value: Any) -> int | None:
    try:
        integer = int(value)
    except (TypeError, ValueError):
        return None
    return integer if integer > 0 else None


def _text(value: Any) -> str:
    return str(value or "").strip()


def _safety_flags() -> dict[str, bool]:
    return {
        "db_write_performed": False,
        "production_db_write_allowed": False,
        "llm_called": False,
        "external_model_called": False,
        "external_api_called": False,
        "relation_generated": False,
        "mechanism_generated": False,
        "mechanism_draft_persisted": False,
        "mechanism_card_created": False,
        "zotero_write_performed": False,
        "vector_store_write_performed": False,
    }
