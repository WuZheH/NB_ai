from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from app.core.paths import DEFAULT_DB_PATH, PROJECT_ROOT
from app.services import inspiration_note_matching_service
from app.services.unit_note_object_processing_service import (
    apply_note_processing_fields,
    columns,
    note_processing_summary,
    table_exists,
)
from app.services.zotero_inspiration_note_service import normalized_selected_text_hash


SOURCE = "zotero_native_annotation"
MODE = "zotero_native_annotation_import"
DEFAULT_ZOTERO_SNAPSHOT_PATH = PROJECT_ROOT / "data" / "zotero" / "snapshot" / "zotero.sqlite"

NO_WRITE_FLAGS = {
    "zotero_db_write_performed": False,
    "llm_called": False,
    "external_llm_called": False,
    "mechanism_generated": False,
    "mechanism_draft_candidates_write_performed": False,
    "vector_store_write_performed": False,
    "lancedb_write_performed": False,
}


def sync_zotero_native_annotations_for_document_or_unit(
    research_db_path: str | Path = DEFAULT_DB_PATH,
    zotero_db_path: str | Path = DEFAULT_ZOTERO_SNAPSHOT_PATH,
    document_id: int | None = None,
    zotero_attachment_key: str | None = None,
    zotero_item_key: str | None = None,
    unit_type: str | None = None,
    page_start: int | None = None,
    page_end: int | None = None,
    section_title: str | None = None,
    apply: bool = False,
) -> dict[str, Any]:
    """Import Zotero native annotation notes for one document or processing unit.

    Zotero is always opened read-only. The NOTEBOOK_AI research DB is opened
    read-only unless ``apply=True`` is explicitly supplied by an import/apply
    flow that is already writing the research DB.
    """
    attachment_key = _clean_text(zotero_attachment_key)
    item_key = _clean_text(zotero_item_key)
    if not attachment_key and not item_key:
        return _skipped_report(
            document_id=document_id,
            unit_type=unit_type,
            page_start=page_start,
            page_end=page_end,
            section_title=section_title,
            apply=apply,
            reason="zotero_attachment_key_missing",
            message="未发现 Zotero attachment，跳过笔记同步。",
        )

    research_path = Path(research_db_path)
    zotero_path = Path(zotero_db_path)
    if not zotero_path.is_file():
        return _skipped_report(
            document_id=document_id,
            unit_type=unit_type,
            page_start=page_start,
            page_end=page_end,
            section_title=section_title,
            apply=apply,
            reason="zotero_snapshot_missing",
            message=f"Zotero snapshot not found: {zotero_path}",
        )

    notes = _read_native_annotations(zotero_path)
    source_notes = [
        note
        for note in notes
        if _matches_attachment(note, attachment_key=attachment_key, item_key=item_key)
    ]
    candidate_notes, filter_warnings = _filter_page_range(
        source_notes,
        page_start=page_start,
        page_end=page_end,
    )

    db_mode = "rw" if apply else "ro"
    with _connect_sqlite(research_path, mode=db_mode) as research_conn:
        research_conn.row_factory = sqlite3.Row
        existing = _existing_note_identities(research_conn)
        prepared = _prepare_notes(
            research_conn,
            candidate_notes,
            document_id=document_id,
            unit_type=unit_type,
            section_title=section_title,
            existing=existing,
        )
        if apply:
            apply_result = _apply_prepared_notes(research_conn, prepared["would_import"])
        else:
            apply_result = {"imported_count": 0, "db_write_performed": False}

    imported_count = int(apply_result.get("imported_count") or 0)
    warnings = list(dict.fromkeys([*filter_warnings, *prepared["warnings"], *apply_result.get("warnings", [])]))
    return {
        "status": "OK",
        "mode": MODE,
        "attempted": True,
        "apply": bool(apply),
        "document_id": document_id,
        "unit_type": unit_type,
        "page_start": page_start,
        "page_end": page_end,
        "section_title": section_title,
        "zotero_attachment_key": attachment_key,
        "zotero_item_key": item_key,
        "candidate_count": len(candidate_notes),
        "source_annotation_count": len(source_notes),
        "would_import_count": len(prepared["would_import"]),
        "imported_count": imported_count,
        "skipped_existing_count": len(prepared["skipped_existing"]),
        "blocked_count": len(prepared["blocked"]),
        "skipped_existing": prepared["skipped_existing"],
        "blocked": prepared["blocked"],
        "notes_preview": prepared["would_import"][:10],
        "summary": note_processing_summary(prepared["would_import"]),
        **note_processing_summary(prepared["would_import"]),
        "warnings": warnings,
        "db_write_performed": bool(apply_result.get("db_write_performed")),
        **NO_WRITE_FLAGS,
    }


def sync_zotero_native_annotations_for_chapters(
    *,
    research_db_path: str | Path = DEFAULT_DB_PATH,
    zotero_db_path: str | Path = DEFAULT_ZOTERO_SNAPSHOT_PATH,
    document_id: int,
    zotero_attachment_key: str | None,
    zotero_item_key: str | None = None,
    chapters: list[Mapping[str, Any]],
    apply: bool = False,
) -> dict[str, Any]:
    per_chapter: list[dict[str, Any]] = []
    totals = {
        "candidate_count": 0,
        "would_import_count": 0,
        "imported_count": 0,
        "skipped_existing_count": 0,
        "blocked_count": 0,
        "annotation_count": 0,
        "user_note_count": 0,
        "evidence_only_count": 0,
        "correction_review_eligible_count": 0,
        "classification_review_eligible_count": 0,
        "object_prompt_user_note_trigger_count": 0,
    }
    warnings: list[str] = []
    for chapter in chapters:
        result = sync_zotero_native_annotations_for_document_or_unit(
            research_db_path=research_db_path,
            zotero_db_path=zotero_db_path,
            document_id=document_id,
            zotero_attachment_key=zotero_attachment_key,
            zotero_item_key=zotero_item_key,
            unit_type="book_chapter",
            page_start=_int_or_none(chapter.get("pdf_page_start")),
            page_end=_int_or_none(chapter.get("pdf_page_end")) or _int_or_none(chapter.get("pdf_page_start")),
            section_title=str(chapter.get("title") or ""),
            apply=apply,
        )
        per_chapter.append(
            {
                "chapter_index": chapter.get("chapter_index"),
                "chapter_title": chapter.get("title"),
                "page_start": chapter.get("pdf_page_start"),
                "page_end": chapter.get("pdf_page_end"),
                **result,
            }
        )
        for key in totals:
            totals[key] += int(result.get(key) or 0)
        warnings.extend(result.get("warnings") or [])
    return {
        "status": "OK",
        "mode": MODE,
        "attempted": bool(zotero_attachment_key or zotero_item_key),
        "apply": bool(apply),
        "document_id": document_id,
        "unit_type": "book_chapter",
        "per_chapter": per_chapter,
        **totals,
        "warnings": list(dict.fromkeys(warnings)),
        "db_write_performed": any(bool(item.get("db_write_performed")) for item in per_chapter),
        **NO_WRITE_FLAGS,
    }


def _read_native_annotations(zotero_db_path: Path) -> list[dict[str, Any]]:
    with _connect_sqlite(zotero_db_path, mode="ro") as conn:
        conn.row_factory = sqlite3.Row
        if table_exists(conn, "annotations"):
            return _read_fixture_annotations(conn)
        if table_exists(conn, "itemAnnotations"):
            return _read_zotero_item_annotations(conn)
    return []


def _read_fixture_annotations(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    cols = columns(conn, "annotations")
    selected = [
        name
        for name in [
            "zotero_annotation_key",
            "zotero_item_key",
            "zotero_attachment_key",
            "pdf_page",
            "page_label",
            "selected_text",
            "note_text",
            "user_tags_json",
            "position_json",
            "bbox_json",
        ]
        if name in cols
    ]
    if not selected:
        return []
    rows = conn.execute(f"SELECT {', '.join(selected)} FROM annotations").fetchall()
    return [_normalize_annotation_row(dict(row)) for row in rows]


def _read_zotero_item_annotations(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    cols = columns(conn, "itemAnnotations")
    if {"itemID", "parentItemID", "text", "comment", "pageLabel", "position"}.issubset(cols) and table_exists(conn, "items"):
        rows = conn.execute(
            """
            SELECT
                ia.itemID AS annotation_item_id,
                annotation_items.key AS zotero_annotation_key,
                parent_attachment.key AS zotero_attachment_key,
                parent_items.key AS zotero_item_key,
                ia.text AS selected_text,
                ia.comment AS note_text,
                ia.pageLabel AS page_label,
                ia.position AS position_json
            FROM itemAnnotations AS ia
            LEFT JOIN items AS annotation_items ON annotation_items.itemID = ia.itemID
            LEFT JOIN items AS parent_attachment ON parent_attachment.itemID = ia.parentItemID
            LEFT JOIN itemAttachments ON itemAttachments.itemID = ia.parentItemID
            LEFT JOIN items AS parent_items ON parent_items.itemID = itemAttachments.parentItemID
            ORDER BY ia.itemID
            """
        ).fetchall()
        tags_by_item = _tags_by_item_id(conn)
        notes = []
        for row in rows:
            data = dict(row)
            data["user_tags_json"] = json.dumps(tags_by_item.get(data.get("annotation_item_id"), []), ensure_ascii=False)
            notes.append(_normalize_annotation_row(data))
        return notes

    selected = [
        name
        for name in [
            "key",
            "annotationKey",
            "itemKey",
            "parentItemKey",
            "annotationText",
            "annotationComment",
            "annotationPageLabel",
            "annotationPosition",
        ]
        if name in cols
    ]
    if not selected:
        return []
    return [
        _normalize_annotation_row(
            {
                "zotero_annotation_key": row.get("annotationKey") or row.get("key"),
                "zotero_item_key": row.get("itemKey"),
                "zotero_attachment_key": row.get("parentItemKey"),
                "page_label": row.get("annotationPageLabel"),
                "selected_text": row.get("annotationText"),
                "note_text": row.get("annotationComment"),
                "user_tags_json": "[]",
                "position_json": row.get("annotationPosition"),
            }
        )
        for row in (dict(item) for item in conn.execute(f"SELECT {', '.join(selected)} FROM itemAnnotations").fetchall())
    ]


def _normalize_annotation_row(row: Mapping[str, Any]) -> dict[str, Any]:
    position = _json_or_none(row.get("position_json") or row.get("bbox_json"))
    page = _int_or_none(row.get("pdf_page")) or _int_or_none(row.get("page_label")) or _page_from_position(position)
    tags = _json_list(row.get("user_tags_json"))
    return {
        "zotero_annotation_key": _clean_text(row.get("zotero_annotation_key")),
        "zotero_item_key": _clean_text(row.get("zotero_item_key")),
        "zotero_attachment_key": _clean_text(row.get("zotero_attachment_key")),
        "pdf_page": page,
        "page_label": _clean_text(row.get("page_label")),
        "selected_text": str(row.get("selected_text") or ""),
        "note_text": str(row.get("note_text") or ""),
        "user_tags": tags,
        "position": position,
    }


def _tags_by_item_id(conn: sqlite3.Connection) -> dict[int, list[str]]:
    if not (table_exists(conn, "itemTags") and table_exists(conn, "tags")):
        return {}
    rows = conn.execute(
        """
        SELECT itemTags.itemID, tags.name
        FROM itemTags
        JOIN tags ON tags.tagID = itemTags.tagID
        ORDER BY itemTags.itemID, tags.name
        """
    ).fetchall()
    result: dict[int, list[str]] = {}
    for item_id, name in rows:
        if name:
            result.setdefault(int(item_id), []).append(str(name))
    return result


def _filter_page_range(
    notes: list[dict[str, Any]],
    *,
    page_start: int | None,
    page_end: int | None,
) -> tuple[list[dict[str, Any]], list[str]]:
    if page_start is None:
        return notes, []
    end = page_end or page_start
    warnings: list[str] = []
    filtered = []
    for note in notes:
        page = _int_or_none(note.get("pdf_page"))
        if page is None:
            warnings.append("page_missing")
            filtered.append(note)
        elif int(page_start) <= page <= int(end):
            filtered.append(note)
    return filtered, list(dict.fromkeys(warnings))


def _prepare_notes(
    research_conn: sqlite3.Connection,
    notes: list[dict[str, Any]],
    *,
    document_id: int | None,
    unit_type: str | None,
    section_title: str | None,
    existing: dict[str, set[Any]],
) -> dict[str, Any]:
    would_import: list[dict[str, Any]] = []
    skipped_existing: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    warnings: list[str] = []
    for note in notes:
        selected_text = str(note.get("selected_text") or "")
        note_text = str(note.get("note_text") or "")
        annotation_key = note.get("zotero_annotation_key")
        selected_hash = normalized_selected_text_hash(selected_text)
        hash_signature = (note.get("zotero_attachment_key"), selected_hash, note_text)
        if not selected_text and not note_text:
            blocked.append({"reason": "selected_text_and_note_text_empty", "zotero_annotation_key": annotation_key})
            continue
        if annotation_key and annotation_key in existing["annotation_keys"]:
            skipped_existing.append({"reason": "duplicate_zotero_annotation_key", "zotero_annotation_key": annotation_key})
            continue
        if hash_signature in existing["hash_signatures"]:
            skipped_existing.append({"reason": "duplicate_attachment_selected_text_hash_note_text", "zotero_annotation_key": annotation_key})
            continue
        record_warnings: list[str] = []
        if not selected_text and note_text:
            record_warnings.append("selected_text_empty_needs_alignment")
        if selected_text and not note_text:
            record_warnings.append("note_text_empty")
        if note.get("pdf_page") is None:
            record_warnings.append("page_missing")
        record = _storage_record(
            research_conn,
            note,
            document_id=document_id,
            unit_type=unit_type,
            section_title=section_title,
            selected_text_hash=selected_hash,
            warnings=record_warnings,
        )
        would_import.append(record)
        warnings.extend(record_warnings)
    return {
        "would_import": would_import,
        "skipped_existing": skipped_existing,
        "blocked": blocked,
        "warnings": list(dict.fromkeys(warnings)),
    }


def _storage_record(
    research_conn: sqlite3.Connection,
    note: Mapping[str, Any],
    *,
    document_id: int | None,
    unit_type: str | None,
    section_title: str | None,
    selected_text_hash: str,
    warnings: list[str],
) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    annotation_key = _clean_text(note.get("zotero_annotation_key"))
    selected_text = str(note.get("selected_text") or "")
    note_text = str(note.get("note_text") or "")
    client_note_id = _client_note_id(annotation_key, note)
    server_note_id = _server_note_id(annotation_key, note, selected_text_hash)
    match = _match_note(
        research_conn,
        note,
        client_note_id=client_note_id,
        server_note_id=server_note_id,
        selected_text_hash=selected_text_hash,
        document_id=document_id,
        warnings=warnings,
    )
    matched_chunk_ids = match["matched_chunk_ids"]
    evidence_alignment_status = match["evidence_alignment_status"]
    match_status = "matched" if evidence_alignment_status in {"matched", "span_matched"} else evidence_alignment_status
    return apply_note_processing_fields({
        "server_note_id": server_note_id,
        "client_note_id": client_note_id,
        "source": SOURCE,
        "zotero_item_key": note.get("zotero_item_key"),
        "zotero_attachment_key": note.get("zotero_attachment_key"),
        "zotero_annotation_key": annotation_key,
        "pdf_page": note.get("pdf_page"),
        "page_label": note.get("page_label"),
        "selected_text": selected_text,
        "selected_text_hash": selected_text_hash,
        "note_text": note_text,
        "user_tags_json": json.dumps(note.get("user_tags") or [], ensure_ascii=False),
        "selection_type": "manual" if not selected_text else "sentence",
        "context_before": None,
        "context_after": None,
        "bbox_json": json.dumps(note.get("position"), ensure_ascii=False) if note.get("position") else None,
        "matched_document_id": document_id,
        "matched_chunk_id": matched_chunk_ids[0] if matched_chunk_ids else None,
        "matched_chunk_ids_json": json.dumps(matched_chunk_ids, ensure_ascii=False),
        "matched_object_ids_json": "[]",
        "sync_status": "synced",
        "match_status": match_status,
        "review_status": "imported",
        "mechanism_status": "not_generated",
        "evidence_alignment_status": evidence_alignment_status,
        "alignment_confidence": match["alignment_confidence"],
        "alignment_method": match["alignment_method"],
        "alignment_warnings_json": json.dumps(list(dict.fromkeys(warnings + match["warnings"])), ensure_ascii=False),
        "created_at": now,
        "updated_at": now,
        "received_at": now,
        "unit_type": unit_type,
        "section_title": section_title,
    })


def _match_note(
    research_conn: sqlite3.Connection,
    note: Mapping[str, Any],
    *,
    client_note_id: str,
    server_note_id: str,
    selected_text_hash: str,
    document_id: int | None,
    warnings: list[str],
) -> dict[str, Any]:
    if not str(note.get("selected_text") or "").strip():
        return _alignment_result("needs_alignment", None, None, [], ["selected_text_empty_matching_skipped"])
    try:
        report = inspiration_note_matching_service.match_inspiration_note_to_document_and_chunk(
            research_conn,
            {
                "client_note_id": client_note_id,
                "server_note_id": server_note_id,
                "source": SOURCE,
                "zotero_item_key": note.get("zotero_item_key"),
                "zotero_attachment_key": note.get("zotero_attachment_key"),
                "zotero_annotation_key": note.get("zotero_annotation_key"),
                "pdf_page": note.get("pdf_page"),
                "page_label": note.get("page_label"),
                "selected_text": note.get("selected_text") or "",
                "selected_text_hash": selected_text_hash,
                "note_text": note.get("note_text") or "",
                "user_tags": note.get("user_tags") or [],
                "selection_type": "sentence",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "bbox": note.get("position"),
            },
            document_id=document_id,
            max_candidates=5,
        )
    except Exception as exc:
        return _alignment_result("needs_alignment", None, None, [], [f"alignment_error:{type(exc).__name__}"])

    candidates = report.get("candidate_chunks") or []
    matched_chunk_ids = [int(item["chunk_id"]) for item in candidates if item.get("chunk_id") is not None]
    confidence = str(report.get("match_confidence") or "none")
    method = str(report.get("match_method") or "unmatched")
    report_warnings = list(report.get("warnings") or [])
    if matched_chunk_ids and confidence in {"high", "medium"}:
        return _alignment_result("matched", confidence, method, matched_chunk_ids, report_warnings)
    if matched_chunk_ids:
        return _alignment_result("span_matched", confidence, method, matched_chunk_ids, report_warnings)
    return _alignment_result("unmatched", confidence, method, [], report_warnings)


def _alignment_result(
    status: str,
    confidence: str | None,
    method: str | None,
    chunk_ids: list[int],
    warnings: list[str],
) -> dict[str, Any]:
    return {
        "evidence_alignment_status": status,
        "alignment_confidence": confidence,
        "alignment_method": method,
        "matched_chunk_ids": chunk_ids,
        "warnings": warnings,
    }


def _apply_prepared_notes(conn: sqlite3.Connection, notes: list[dict[str, Any]]) -> dict[str, Any]:
    if not table_exists(conn, "zotero_inspiration_notes"):
        return {
            "imported_count": 0,
            "db_write_performed": False,
            "warnings": ["zotero_inspiration_notes_schema_missing"],
        }
    available = columns(conn, "zotero_inspiration_notes")
    inserted = 0
    for note in notes:
        insert_cols = [key for key in note if key in available]
        if not insert_cols:
            continue
        placeholders = ", ".join("?" for _ in insert_cols)
        conn.execute(
            f"INSERT INTO zotero_inspiration_notes ({', '.join(insert_cols)}) VALUES ({placeholders})",
            [note[key] for key in insert_cols],
        )
        inserted += 1
    conn.commit()
    return {"imported_count": inserted, "db_write_performed": inserted > 0, "warnings": []}


def _existing_note_identities(conn: sqlite3.Connection) -> dict[str, set[Any]]:
    if not table_exists(conn, "zotero_inspiration_notes"):
        return {"annotation_keys": set(), "hash_signatures": set()}
    cols = columns(conn, "zotero_inspiration_notes")
    selected = [name for name in ["zotero_attachment_key", "zotero_annotation_key", "selected_text_hash", "note_text"] if name in cols]
    if not selected:
        return {"annotation_keys": set(), "hash_signatures": set()}
    rows = conn.execute(f"SELECT {', '.join(selected)} FROM zotero_inspiration_notes").fetchall()
    return {
        "annotation_keys": {row["zotero_annotation_key"] for row in rows if "zotero_annotation_key" in row.keys() and row["zotero_annotation_key"]},
        "hash_signatures": {
            (
                row["zotero_attachment_key"] if "zotero_attachment_key" in row.keys() else None,
                row["selected_text_hash"] if "selected_text_hash" in row.keys() else None,
                row["note_text"] if "note_text" in row.keys() else None,
            )
            for row in rows
            if "selected_text_hash" in row.keys() and row["selected_text_hash"]
        },
    }


def _matches_attachment(note: Mapping[str, Any], *, attachment_key: str | None, item_key: str | None) -> bool:
    return bool(
        (attachment_key and note.get("zotero_attachment_key") == attachment_key)
        or (item_key and note.get("zotero_item_key") == item_key)
    )


def _connect_sqlite(path: Path, *, mode: str) -> sqlite3.Connection:
    resolved = path.resolve(strict=False)
    conn = sqlite3.connect(f"file:{resolved.as_posix()}?mode={mode}", uri=True)
    if mode == "ro":
        conn.execute("PRAGMA query_only = ON")
    return conn


def _skipped_report(
    *,
    document_id: int | None,
    unit_type: str | None,
    page_start: int | None,
    page_end: int | None,
    section_title: str | None,
    apply: bool,
    reason: str,
    message: str,
) -> dict[str, Any]:
    return {
        "status": "SKIPPED",
        "mode": MODE,
        "attempted": False,
        "apply": bool(apply),
        "document_id": document_id,
        "unit_type": unit_type,
        "page_start": page_start,
        "page_end": page_end,
        "section_title": section_title,
        "reason": reason,
        "message": message,
        "candidate_count": 0,
        "would_import_count": 0,
        "imported_count": 0,
        "skipped_existing_count": 0,
        "blocked_count": 0,
        "warnings": [reason],
        "db_write_performed": False,
        **NO_WRITE_FLAGS,
    }


def _client_note_id(annotation_key: str | None, note: Mapping[str, Any]) -> str:
    if annotation_key:
        return f"zinsp_client_zotero_annotation_{annotation_key}"
    return "zinsp_client_zotero_annotation_" + _short_hash(_identity_payload(note), 24)


def _server_note_id(annotation_key: str | None, note: Mapping[str, Any], selected_text_hash: str) -> str:
    if annotation_key:
        identity = f"annotation:{annotation_key}"
    else:
        identity = json.dumps(
            {
                "attachment": note.get("zotero_attachment_key"),
                "selected_text_hash": selected_text_hash,
                "note_text": note.get("note_text"),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    return "zinsp_zotero_annotation_" + _short_hash(identity, 32)


def _identity_payload(note: Mapping[str, Any]) -> str:
    return json.dumps(
        {
            "attachment": note.get("zotero_attachment_key"),
            "annotation": note.get("zotero_annotation_key"),
            "selected_text": note.get("selected_text"),
            "note_text": note.get("note_text"),
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _short_hash(value: str, length: int) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]


def _page_from_position(position: Any) -> int | None:
    if not isinstance(position, Mapping):
        return None
    page_index = _int_or_none(position.get("pageIndex"))
    return page_index + 1 if page_index is not None else None


def _json_or_none(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None


def _json_list(value: Any) -> list[str]:
    parsed = _json_or_none(value)
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed if str(item).strip()]


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    match = re.search(r"\d+", str(value))
    return int(match.group(0)) if match else None


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
