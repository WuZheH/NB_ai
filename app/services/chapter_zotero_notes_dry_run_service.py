from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Mapping

from app.core.paths import DEFAULT_DB_PATH
from app.services import zotero_native_annotation_import_service as native_service
from app.services.unit_note_object_processing_service import (
    NOTE_ROLE_BLOCKED,
    ZOTERO_NATIVE_ANNOTATION_SOURCE,
    columns,
    connect_readonly,
    document_row,
    document_source_keys,
    note_processing_fields,
    safety_flags,
    table_exists,
)
from app.services.zotero_inspiration_note_service import normalized_selected_text_hash


DEFAULT_ZOTERO_SNAPSHOT_PATH = native_service.DEFAULT_ZOTERO_SNAPSHOT_PATH
DUPLICATE_POLICY = "zotero_annotation_key_or_attachment_selected_text_hash_note_text"


class ChapterZoteroNotesDryRunError(ValueError):
    pass


def build_chapter_zotero_notes_dry_run(
    *,
    research_db_path: str | Path = DEFAULT_DB_PATH,
    zotero_db_path: str | Path = DEFAULT_ZOTERO_SNAPSHOT_PATH,
    document_id: int,
    chapter_id: int,
) -> dict[str, Any]:
    research_path = Path(research_db_path)
    zotero_path = Path(zotero_db_path)
    with connect_readonly(research_path) as research_conn:
        document = document_row(research_conn, document_id)
        if not document:
            raise ChapterZoteroNotesDryRunError(f"document not found: {document_id}")
        chapter = _chapter_row(research_conn, document_id, chapter_id)
        if not chapter:
            raise ChapterZoteroNotesDryRunError(f"chapter not found: document_id={document_id}, chapter_id={chapter_id}")
        source_keys = document_source_keys(research_conn, document_id)
        attachment_key = _clean(source_keys.get("zotero_attachment_key"))
        item_key = _clean(source_keys.get("zotero_item_key") or document.get("zotero_key"))
        chunk_map = _chapter_chunk_map(research_conn, document_id, chapter_id)
        existing = native_service._existing_note_identities(research_conn)
        raw_notes = native_service._read_native_annotations(zotero_path)
        source_notes = [
            note for note in raw_notes
            if native_service._matches_attachment(note, attachment_key=attachment_key, item_key=item_key)
        ]
        chapter_notes, filter_warnings = native_service._filter_page_range(
            source_notes,
            page_start=_int_or_none(chapter.get("pdf_page_start")),
            page_end=_int_or_none(chapter.get("pdf_page_end")),
        )
        prepared = native_service._prepare_notes(
            research_conn,
            chapter_notes,
            document_id=document_id,
            unit_type="book_chapter",
            section_title=str(chapter.get("title") or ""),
            existing=existing,
        )
        existing_note_index = _existing_note_identity_index(research_conn)
        prepared["skipped_existing"] = _enrich_skipped_existing(
            prepared["skipped_existing"],
            existing_note_index,
            chapter_notes,
        )
        mappings = _candidate_mappings(
            research_conn,
            chapter_notes,
            document_id=document_id,
            chapter=chapter,
            chunk_map=chunk_map,
            prepared=prepared,
        )

    note_summary = _role_summary(chapter_notes)
    with_page_count = sum(1 for note in chapter_notes if _int_or_none(note.get("pdf_page")) is not None)
    without_page_count = len(chapter_notes) - with_page_count
    empty_note_text_count = sum(1 for note in chapter_notes if not str(note.get("note_text") or "").strip())
    user_note_count = note_summary["user_note_count"]
    evidence_only_count = note_summary["evidence_only_count"]
    flags = {
        **safety_flags(),
        "object_candidates_generated": False,
        "mechanism_generated": False,
        "zotero_db_write_performed": False,
        "vector_store_write_performed": False,
        "db_write_performed": False,
    }
    no_notes_in_scope = (
        len(chapter_notes) == 0
        and len(prepared["would_import"]) == 0
        and len(prepared["skipped_existing"]) == 0
        and len(prepared["blocked"]) == 0
    )
    return {
        "status": "NO_NOTES_IN_SCOPE" if no_notes_in_scope else "OK",
        "mode": "chapter_zotero_notes_dry_run",
        "dry_run": True,
        "apply_requested": False,
        "reason": "no_notes_in_scope" if no_notes_in_scope else None,
        "message": "当前章没有 Zotero 笔记可导入。" if no_notes_in_scope else None,
        "document": document,
        "document_id": document_id,
        "chapter_id": chapter_id,
        "chapter_index": chapter.get("chapter_index"),
        "chapter_title": chapter.get("title"),
        "page_start": chapter.get("pdf_page_start"),
        "page_end": chapter.get("pdf_page_end"),
        "zotero_item_key": item_key,
        "zotero_attachment_key": attachment_key,
        "total_annotations_in_attachment": len(source_notes),
        "chapter_annotations_count": len(chapter_notes),
        "chapter8_annotations_count": len(chapter_notes),
        "chapter_user_note_count": user_note_count,
        "chapter8_user_note_count": user_note_count,
        "chapter_evidence_only_count": evidence_only_count,
        "chapter8_evidence_only_count": evidence_only_count,
        "chapter_empty_note_text_count": empty_note_text_count,
        "chapter8_empty_note_text_count": empty_note_text_count,
        "chapter_annotations_with_page_count": with_page_count,
        "chapter8_annotations_with_page_count": with_page_count,
        "chapter_annotations_without_page_count": without_page_count,
        "chapter8_annotations_without_page_count": without_page_count,
        "would_insert_count": len(prepared["would_import"]),
        "would_skip_existing_count": len(prepared["skipped_existing"]),
        "would_block_count": len(prepared["blocked"]),
        "duplicate_policy": DUPLICATE_POLICY,
        "candidate_mappings": mappings,
        "mapping_quality_summary": _mapping_quality_summary(mappings, chapter_id=chapter_id),
        "note_first_gates": _note_first_gates(user_note_count=user_note_count, evidence_only_count=evidence_only_count),
        "skipped_existing": prepared["skipped_existing"],
        "blocked": prepared["blocked"],
        "warnings": list(dict.fromkeys([*filter_warnings, *prepared["warnings"]])),
        "zotero_read_boundary": {
            "zotero_connection": "file_uri_mode_ro_query_only",
            "research_connection": "file_uri_mode_ro_query_only",
        },
        **flags,
    }


def apply_chapter_zotero_notes(
    *,
    research_db_path: str | Path = DEFAULT_DB_PATH,
    zotero_db_path: str | Path = DEFAULT_ZOTERO_SNAPSHOT_PATH,
    document_id: int,
    chapter_id: int,
    expected_would_insert_count: int | None = None,
) -> dict[str, Any]:
    dry_run = build_chapter_zotero_notes_dry_run(
        research_db_path=research_db_path,
        zotero_db_path=zotero_db_path,
        document_id=document_id,
        chapter_id=chapter_id,
    )
    if expected_would_insert_count is not None and dry_run["would_insert_count"] != int(expected_would_insert_count):
        return {
            **dry_run,
            "status": "BLOCKED",
            "apply_requested": True,
            "apply_blocked_reason": "expected_would_insert_count_mismatch",
            "expected_would_insert_count": expected_would_insert_count,
            "inserted_count": 0,
            "db_write_performed": False,
        }
    if dry_run["would_block_count"]:
        return {
            **dry_run,
            "status": "BLOCKED",
            "apply_requested": True,
            "apply_blocked_reason": "dry_run_has_blocked_annotations",
            "inserted_count": 0,
            "db_write_performed": False,
        }

    research_path = Path(research_db_path)
    before_counts = _post_apply_counts(research_path, document_id=document_id, chapter_id=chapter_id)
    apply_result = native_service.sync_zotero_native_annotations_for_document_or_unit(
        research_db_path=research_path,
        zotero_db_path=zotero_db_path,
        document_id=document_id,
        zotero_attachment_key=dry_run["zotero_attachment_key"],
        zotero_item_key=dry_run["zotero_item_key"],
        unit_type="book_chapter",
        page_start=dry_run["page_start"],
        page_end=dry_run["page_end"],
        section_title=dry_run["chapter_title"],
        apply=True,
    )
    after_counts = _post_apply_counts(research_path, document_id=document_id, chapter_id=chapter_id)
    return {
        "status": "OK",
        "mode": "chapter_zotero_notes_apply",
        "dry_run": False,
        "apply_requested": True,
        "document_id": document_id,
        "chapter_id": chapter_id,
        "chapter_index": dry_run["chapter_index"],
        "chapter_title": dry_run["chapter_title"],
        "page_start": dry_run["page_start"],
        "page_end": dry_run["page_end"],
        "zotero_item_key": dry_run["zotero_item_key"],
        "zotero_attachment_key": dry_run["zotero_attachment_key"],
        "inserted_count": int(apply_result.get("imported_count") or 0),
        "skipped_existing_count": int(apply_result.get("skipped_existing_count") or 0),
        "blocked_count": int(apply_result.get("blocked_count") or 0),
        "candidate_count": int(apply_result.get("candidate_count") or 0),
        "duplicate_policy": DUPLICATE_POLICY,
        "before_counts": before_counts,
        "after_counts": after_counts,
        "only_table_write_scope": "zotero_inspiration_notes",
        "object_candidates_generated": False,
        "mechanism_generated": False,
        "mechanism_draft_candidates_write_performed": False,
        "vector_store_write_performed": False,
        "zotero_db_write_performed": False,
        "llm_called": False,
        "external_llm_called": False,
        "ocr_or_marker_performed": False,
        "db_write_performed": bool(apply_result.get("db_write_performed")),
        "warnings": apply_result.get("warnings") or dry_run.get("warnings") or [],
    }


def _chapter_row(conn: sqlite3.Connection, document_id: int, chapter_id: int) -> dict[str, Any]:
    if not table_exists(conn, "book_chapters"):
        return {}
    cols = columns(conn, "book_chapters")
    selected = [name for name in ["id", "chapter_index", "title", "pdf_page_start", "pdf_page_end"] if name in cols]
    if not selected:
        return {}
    row = conn.execute(
        f"SELECT {', '.join(selected)} FROM book_chapters WHERE document_id = ? AND id = ?",
        (document_id, chapter_id),
    ).fetchone()
    return dict(row) if row else {}


def _post_apply_counts(research_db_path: Path, *, document_id: int, chapter_id: int) -> dict[str, Any]:
    with connect_readonly(research_db_path) as conn:
        if not table_exists(conn, "zotero_inspiration_notes"):
            notes_count = 0
        else:
            notes_count = int(
                conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM zotero_inspiration_notes zin
                    LEFT JOIN knowledge_chunks kc ON kc.id = zin.matched_chunk_id
                    WHERE zin.matched_document_id = ?
                      AND (kc.chapter_id = ? OR zin.pdf_page BETWEEN (
                        SELECT pdf_page_start FROM book_chapters WHERE id = ?
                      ) AND (
                        SELECT pdf_page_end FROM book_chapters WHERE id = ?
                      ))
                    """,
                    (document_id, chapter_id, chapter_id, chapter_id),
                ).fetchone()[0]
            )
        object_candidates = int(
            conn.execute("SELECT COUNT(*) FROM object_candidates WHERE document_id = ?", (document_id,)).fetchone()[0]
        ) if table_exists(conn, "object_candidates") else 0
        mechanism_drafts = int(
            conn.execute("SELECT COUNT(*) FROM mechanism_draft_candidates").fetchone()[0]
        ) if table_exists(conn, "mechanism_draft_candidates") else 0
        return {
            "zotero_inspiration_notes_scope_count": notes_count,
            "object_candidates_document_count": object_candidates,
            "mechanism_draft_candidates_total_count": mechanism_drafts,
        }


def _chapter_chunk_map(conn: sqlite3.Connection, document_id: int, chapter_id: int) -> dict[int, dict[str, Any]]:
    if not table_exists(conn, "knowledge_chunks"):
        return {}
    cols = columns(conn, "knowledge_chunks")
    id_col = "chunk_id" if "chunk_id" in cols else "id"
    selected = [
        name
        for name in [id_col, "chapter_id", "chunk_text", "text", "pdf_page_start", "pdf_page_end"]
        if name in cols
    ]
    rows = conn.execute(
        f"SELECT {', '.join(selected)} FROM knowledge_chunks WHERE document_id = ? AND chapter_id = ?",
        (document_id, chapter_id),
    ).fetchall()
    result: dict[int, dict[str, Any]] = {}
    for row in rows:
        data = dict(row)
        result[int(data[id_col])] = data
    return result


def _candidate_mappings(
    conn: sqlite3.Connection,
    notes: list[dict[str, Any]],
    *,
    document_id: int,
    chapter: Mapping[str, Any],
    chunk_map: dict[int, dict[str, Any]],
    prepared: dict[str, Any],
) -> list[dict[str, Any]]:
    skipped_by_key = {
        item.get("zotero_annotation_key"): item
        for item in prepared["skipped_existing"]
        if item.get("zotero_annotation_key")
    }
    blocked_by_key = {
        item.get("zotero_annotation_key"): item
        for item in prepared["blocked"]
        if item.get("zotero_annotation_key")
    }
    mappings: list[dict[str, Any]] = []
    for note in notes:
        annotation_key = note.get("zotero_annotation_key")
        selected_hash = normalized_selected_text_hash(str(note.get("selected_text") or ""))
        duplicate_status = "new"
        block_reason = None
        if annotation_key in skipped_by_key:
            duplicate_status = "skip_existing"
        if annotation_key in blocked_by_key:
            duplicate_status = "blocked"
            block_reason = blocked_by_key[annotation_key].get("reason")

        record: dict[str, Any] = {}
        if duplicate_status != "blocked":
            record = native_service._storage_record(
                conn,
                note,
                document_id=document_id,
                unit_type="book_chapter",
                section_title=str(chapter.get("title") or ""),
                selected_text_hash=selected_hash,
                warnings=[],
            )
        processing = note_processing_fields({
            "source": ZOTERO_NATIVE_ANNOTATION_SOURCE,
            "selected_text": note.get("selected_text"),
            "note_text": note.get("note_text"),
        })
        role = NOTE_ROLE_BLOCKED if duplicate_status == "blocked" else processing["note_processing_role"]
        matched_chunk_ids = _json_list(record.get("matched_chunk_ids_json"))
        matched_chunk_id = _first_int(matched_chunk_ids)
        matched_chapter_id = int(chapter["id"]) if matched_chunk_id in chunk_map else None
        warnings = _json_list(record.get("alignment_warnings_json"))
        page = _int_or_none(note.get("pdf_page"))
        if not _page_in_range(page, chapter):
            warnings.append("page_outside_chapter_range")
        if matched_chunk_id and matched_chunk_id not in chunk_map:
            warnings.append("matched_chunk_outside_chapter")
        mappings.append({
            "zotero_annotation_key": annotation_key,
            "page": page,
            "selected_text_preview": _preview(note.get("selected_text")),
            "note_text_preview": _preview(note.get("note_text")),
            "note_processing_role": role,
            "duplicate_status": duplicate_status,
            "block_reason": block_reason,
            "matched_document_id": record.get("matched_document_id") or document_id,
            "matched_chapter_id": matched_chapter_id,
            "matched_chunk_id": matched_chunk_id,
            "alignment_confidence": record.get("alignment_confidence"),
            "alignment_method": record.get("alignment_method"),
            "evidence_alignment_status": record.get("evidence_alignment_status"),
            "object_candidate_gate": _object_candidate_gate(role),
            "warnings": list(dict.fromkeys(warnings)),
        })
    return mappings


def _role_summary(notes: list[Mapping[str, Any]]) -> dict[str, int]:
    user_note_count = 0
    evidence_only_count = 0
    blocked_count = 0
    for note in notes:
        fields = note_processing_fields({
            "source": ZOTERO_NATIVE_ANNOTATION_SOURCE,
            "selected_text": note.get("selected_text"),
            "note_text": note.get("note_text"),
        })
        if fields["note_processing_role"] == "user_note":
            user_note_count += 1
        elif fields["note_processing_role"] == "evidence_only":
            evidence_only_count += 1
        else:
            blocked_count += 1
    return {
        "user_note_count": user_note_count,
        "evidence_only_count": evidence_only_count,
        "blocked_count": blocked_count,
    }


def _mapping_quality_summary(mappings: list[dict[str, Any]], *, chapter_id: int) -> dict[str, int]:
    matched = [
        item for item in mappings
        if item.get("matched_chapter_id") and item.get("matched_chunk_id")
    ]
    return {
        "candidate_mapping_count": len(mappings),
        "matched_chunk_count": len(matched),
        "unmatched_count": sum(1 for item in mappings if not item.get("matched_chunk_id")),
        "matched_target_chapter_count": sum(1 for item in mappings if item.get("matched_chapter_id") == chapter_id),
        "matched_chapter_id_69_count": sum(1 for item in mappings if item.get("matched_chapter_id") == 69),
        "user_note_mapping_count": sum(1 for item in mappings if item.get("note_processing_role") == "user_note"),
        "evidence_only_mapping_count": sum(1 for item in mappings if item.get("note_processing_role") == "evidence_only"),
        "blocked_mapping_count": sum(1 for item in mappings if item.get("note_processing_role") == "blocked"),
        "page_outside_range_warning_count": sum(
            1 for item in mappings if "page_outside_chapter_range" in (item.get("warnings") or [])
        ),
    }


def _note_first_gates(*, user_note_count: int, evidence_only_count: int) -> dict[str, Any]:
    note_correction_ready = user_note_count > 0
    return {
        "note_correction_review": "ready_for_note_correction_package" if note_correction_ready else "blocked_no_notes_in_scope",
        "note_classification_review": "blocked_notes_not_corrected" if note_correction_ready else "blocked_no_notes_in_scope",
        "object_candidate_generation": "planned_tri_source_not_implemented",
        "note_anchored_object_generation": "blocked_until_note_correction_and_classification",
        "highlight_anchored_object_generation": "planned_not_implemented_requires_highlight_evidence",
        "chapter_global_object_generation": "planned_not_implemented_requires_full_chapter_chunks",
        "unified_object_review": "planned_required_before_relations_or_insights",
        "legacy_chapter_object_bundle": "retired_legacy_chunk_only_bundle",
        "user_notes_unlock_note_correction_dry_run": note_correction_ready,
        "prompt_generated": False,
        "object_candidates_generated": False,
        "mechanism_generated": False,
        "evidence_only_count": evidence_only_count,
    }


def _existing_note_identity_index(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    if not table_exists(conn, "zotero_inspiration_notes"):
        return {}
    note_cols = columns(conn, "zotero_inspiration_notes")
    selected = [
        name
        for name in [
            "id",
            "server_note_id",
            "client_note_id",
            "zotero_annotation_key",
            "zotero_attachment_key",
            "selected_text_hash",
            "note_text",
        ]
        if name in note_cols
    ]
    if not selected:
        return {}
    rows = conn.execute(
        f"SELECT {', '.join(selected)} FROM zotero_inspiration_notes"
    ).fetchall()
    index: dict[str, dict[str, Any]] = {}
    for row in rows:
        data = dict(row)
        annotation_key = _clean(data.get("zotero_annotation_key"))
        if annotation_key:
            index[f"annotation:{annotation_key}"] = data
        hash_key = _hash_signature_key(
            data.get("zotero_attachment_key"),
            data.get("selected_text_hash"),
            data.get("note_text"),
        )
        if hash_key:
            index[f"hash:{hash_key}"] = data
    return index


def _enrich_skipped_existing(
    skipped_existing: list[dict[str, Any]],
    existing_index: Mapping[str, Mapping[str, Any]],
    source_notes: list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    source_by_annotation = {
        _clean(note.get("zotero_annotation_key")): note
        for note in source_notes
        if _clean(note.get("zotero_annotation_key"))
    }
    enriched: list[dict[str, Any]] = []
    for item in skipped_existing:
        next_item = dict(item)
        annotation_key = _clean(next_item.get("zotero_annotation_key"))
        existing = existing_index.get(f"annotation:{annotation_key}") if annotation_key else None
        if not existing and annotation_key:
            source_note = source_by_annotation.get(annotation_key) or {}
            hash_key = _hash_signature_key(
                source_note.get("zotero_attachment_key"),
                normalized_selected_text_hash(str(source_note.get("selected_text") or "")),
                str(source_note.get("note_text") or ""),
            )
            existing = existing_index.get(f"hash:{hash_key}") if hash_key else None
        if existing:
            next_item["existing_note_row_id"] = existing.get("id")
            next_item["server_note_id"] = existing.get("server_note_id")
            next_item["client_note_id"] = existing.get("client_note_id")
        else:
            next_item.setdefault("identity_lookup_reason", "existing_note_identity_not_found_for_duplicate_policy")
        enriched.append(next_item)
    return enriched


def _hash_signature_key(attachment_key: Any, selected_text_hash: Any, note_text: Any) -> str | None:
    if not selected_text_hash:
        return None
    return json.dumps(
        [attachment_key, selected_text_hash, note_text],
        ensure_ascii=False,
        sort_keys=False,
    )


def _object_candidate_gate(role: str) -> str:
    if role == "evidence_only":
        return "planned_highlight_anchored_object_not_implemented"
    if role == "user_note":
        return "blocked_note_anchored_until_note_review"
    return "blocked_annotation_invalid"


def _preview(value: Any, limit: int = 160) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "..."


def _json_list(value: Any) -> list[int | str]:
    if isinstance(value, list):
        return value
    if not isinstance(value, str) or not value.strip():
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def _first_int(values: list[int | str]) -> int | None:
    for value in values:
        number = _int_or_none(value)
        if number is not None:
            return number
    return None


def _page_in_range(page: int | None, chapter: Mapping[str, Any]) -> bool:
    if page is None:
        return True
    start = _int_or_none(chapter.get("pdf_page_start"))
    end = _int_or_none(chapter.get("pdf_page_end")) or start
    if start is None:
        return True
    return start <= page <= int(end)


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(str(value).strip())
    except ValueError:
        return None


def _clean(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None
