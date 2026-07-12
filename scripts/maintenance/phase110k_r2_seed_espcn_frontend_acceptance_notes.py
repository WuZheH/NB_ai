from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
import unicodedata
from pathlib import Path
from typing import Any, Iterable, Mapping

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.paths import DEFAULT_DB_PATH


MODE = "phase110k_r2_espcn_synthetic_note_seed_v1"
DEFAULT_SEED_PATH = PROJECT_ROOT / "data" / "seeds" / "espcn10_frontend_acceptance_notes.json"
DEFAULT_DOCUMENT_TITLE = (
    "Real-Time Single Image and Video Super-Resolution Using an Efficient "
    "Sub-Pixel Convolutional Neural Network"
)
SEED_TAG = "espcn10_frontend_acceptance_seed"
SEED_SOURCE = "synthetic_acceptance_seed"
CREATED_AT = "2026-05-29T00:00:00Z"
ALIGNMENT_METHOD = "synthetic_acceptance_seed_exact_page_note"
BASE_ALIGNMENT_WARNING = "synthetic_seed_not_zotero_reader_capture"
REQUIRED_TAGS = {SEED_TAG, "espcn", "super_resolution", "灵感"}
NOTE_TYPE_TAGS = {
    "memory_note",
    "connection_note",
    "mechanism_note",
    "research_idea_note",
}
COUNT_TABLES = (
    "zotero_inspiration_notes",
    "mechanism_draft_candidates",
    "knowledge_chunks",
)
REQUIRED_INSERT_COLUMNS = {
    "server_note_id",
    "client_note_id",
    "source",
    "zotero_item_key",
    "zotero_attachment_key",
    "zotero_annotation_key",
    "pdf_page",
    "page_label",
    "selected_text",
    "selected_text_hash",
    "note_text",
    "user_tags_json",
    "selection_type",
    "context_before",
    "context_after",
    "bbox_json",
    "matched_document_id",
    "matched_chunk_id",
    "matched_object_ids_json",
    "sync_status",
    "match_status",
    "review_status",
    "mechanism_status",
    "created_at",
    "updated_at",
    "received_at",
    "matched_chunk_ids_json",
    "evidence_alignment_status",
    "alignment_confidence",
    "alignment_method",
    "alignment_warnings_json",
}
REQUIRED_CHUNK_COLUMNS = {"id", "document_id", "chunk_text"}
INSERT_COLUMNS = (
    "server_note_id",
    "client_note_id",
    "source",
    "zotero_item_key",
    "zotero_attachment_key",
    "zotero_annotation_key",
    "pdf_page",
    "page_label",
    "selected_text",
    "selected_text_hash",
    "note_text",
    "user_tags_json",
    "selection_type",
    "context_before",
    "context_after",
    "bbox_json",
    "matched_document_id",
    "matched_chunk_id",
    "matched_object_ids_json",
    "sync_status",
    "match_status",
    "review_status",
    "mechanism_status",
    "created_at",
    "updated_at",
    "received_at",
    "matched_chunk_ids_json",
    "evidence_alignment_status",
    "alignment_confidence",
    "alignment_method",
    "alignment_warnings_json",
)

OBJECT_TERMS_BY_SEED_NOTE_ID = {
    "espcn10_01": ("Bicubic Interpolation", "Super-Resolution", "SRCNN"),
    "espcn10_02": ("ESPCN", "Sub-pixel Convolution", "Super-Resolution"),
    "espcn10_03": ("ESPCN", "Sub-pixel Convolution", "Super-Resolution"),
    "espcn10_04": ("Periodic Shuffling", "Pixel Shuffle", "Sub-pixel Convolution"),
    "espcn10_05": ("Bicubic Interpolation", "SRCNN", "Deconvolution", "Sub-pixel Convolution"),
    "espcn10_06": ("ESPCN", "Sub-pixel Convolution", "Super-Resolution"),
    "espcn10_07": ("ESPCN", "SRCNN", "ImageNet", "PSNR"),
    "espcn10_08": ("ESPCN", "SRCNN", "Super-Resolution"),
    "espcn10_09": ("ESPCN", "Super-Resolution"),
    "espcn10_10": ("ESPCN", "3D Convolution", "Super-Resolution", "Xiph"),
}


def build_seed_report(
    db_path: str | Path = DEFAULT_DB_PATH,
    *,
    seed_json: str | Path = DEFAULT_SEED_PATH,
    apply: bool = False,
    cleanup_only: bool = False,
    document_title: str = DEFAULT_DOCUMENT_TITLE,
    document_id: int | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    db = Path(db_path)
    seed_path = Path(seed_json)
    seed = _load_seed(seed_path)
    notes = list(seed["notes"])
    if limit is not None:
        notes = notes[: max(0, int(limit))]

    report = _base_report(
        db,
        seed_path,
        apply=apply,
        cleanup_only=cleanup_only,
        document_title=document_title,
        requested_document_id=document_id,
        notes_planned=len(notes),
    )
    report["seed_validation"] = _validate_seed(seed)
    if report["seed_validation"]["blockers"]:
        report["status"] = "BLOCKED"
        report["blockers"].extend(report["seed_validation"]["blockers"])
        return report

    if not db.exists():
        report["status"] = "BLOCKED"
        report["blockers"].append("database_not_found")
        report["message"] = "research_memory.db 不存在；请先确认本地数据库路径。"
        return report

    connect_mode = "rw" if apply else "ro"
    with sqlite3.connect(_sqlite_uri(db, mode=connect_mode), uri=True) as conn:
        conn.row_factory = sqlite3.Row
        schema_before = _schema_snapshot(conn)
        preflight = _preflight(
            conn,
            require_document=not cleanup_only,
            document_title=document_title,
            document_id=document_id,
        )
        report.update(
            {
                "document_found": bool(preflight.get("target_document")),
                "document_id": (
                    preflight.get("target_document") or {}
                ).get("document_id"),
                "document_title": (
                    preflight.get("target_document") or {}
                ).get("title"),
                "preflight": preflight,
            }
        )
        report["counts_before"] = preflight["counts_before"]
        report["blockers"].extend(preflight["blockers"])
        report["warnings"].extend(preflight["warnings"])
        if report["blockers"]:
            report["status"] = "BLOCKED"
            report["message"] = _blocked_message(report["blockers"])
            report["postflight"] = _postflight(
                conn,
                schema_before=schema_before,
                baseline_counts=preflight["counts_before"],
                document_id=report["document_id"],
            )
            return report

        old_rows = _tagged_seed_rows(conn)
        deletable = [row for row in old_rows if row["deletable"]]
        report["old_seeded_notes_found"] = len(old_rows)
        report["old_seeded_note_rows"] = old_rows
        if apply and deletable:
            deleted = _delete_old_seed_rows(conn, deletable)
            report["old_seeded_notes_deleted"] = deleted
            report["db_write_performed"] = report["db_write_performed"] or deleted > 0
            report["zotero_inspiration_notes_write_performed"] = (
                report["zotero_inspiration_notes_write_performed"] or deleted > 0
            )
        if cleanup_only:
            conn.commit()
            report["status"] = "CLEANUP_APPLIED" if apply else "DRY_RUN"
            report["postflight"] = _postflight(
                conn,
                schema_before=schema_before,
                baseline_counts=preflight["counts_before"],
                document_id=report["document_id"],
            )
            _apply_postflight_guards(report)
            return report

        target = preflight["target_document"]
        plan = _build_note_records(
            conn,
            notes,
            document_id=int(target["document_id"]),
            zotero_item_key=target.get("zotero_item_key"),
            zotero_attachment_key=target.get("zotero_attachment_key"),
        )
        report["planned_note_rows"] = plan["note_summaries"]
        report["notes_blocked"] = plan["blocked_notes"]
        report["note_type_distribution"] = _note_type_distribution(plan["note_summaries"])
        report["chunk_alignment_summary"] = _chunk_alignment_summary(plan["note_summaries"])
        report["object_resolution_summary"] = _object_resolution_summary(plan["note_summaries"])
        report["warnings"].extend(plan["warnings"])
        if apply and plan["records"]:
            inserted = _insert_records(conn, plan["records"])
            report["notes_inserted"] = inserted
            report["db_write_performed"] = report["db_write_performed"] or inserted > 0
            report["zotero_inspiration_notes_write_performed"] = (
                report["zotero_inspiration_notes_write_performed"] or inserted > 0
            )
        report["status"] = "APPLIED" if apply else "DRY_RUN"
        conn.commit()
        report["postflight"] = _postflight(
            conn,
            schema_before=schema_before,
            baseline_counts=preflight["counts_before"],
            document_id=report["document_id"],
        )
        _apply_postflight_guards(report)
    return report


def _base_report(
    db: Path,
    seed_path: Path,
    *,
    apply: bool,
    cleanup_only: bool,
    document_title: str,
    requested_document_id: int | None,
    notes_planned: int,
) -> dict[str, Any]:
    return {
        "status": "DRY_RUN",
        "mode": MODE,
        "db_path": str(db),
        "seed_json": str(seed_path),
        "apply": apply,
        "cleanup_only": cleanup_only,
        "document_title_query": document_title,
        "requested_document_id": requested_document_id,
        "document_found": False,
        "document_id": None,
        "document_title": None,
        "notes_planned": notes_planned,
        "notes_inserted": 0,
        "notes_blocked": [],
        "old_seeded_notes_found": 0,
        "old_seeded_notes_deleted": 0,
        "note_type_distribution": {},
        "chunk_alignment_summary": {},
        "object_resolution_summary": {},
        "warnings": [],
        "blockers": [],
        "db_write_performed": False,
        "zotero_inspiration_notes_write_performed": False,
        "mechanism_draft_candidates_write_performed": False,
        "knowledge_chunks_write_performed": False,
        "llm_called": False,
        "mechanism_generated": False,
        "vector_store_write_performed": False,
        "schema_write_performed": False,
    }


def _load_seed(seed_path: Path) -> dict[str, Any]:
    with seed_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _validate_seed(seed: Mapping[str, Any]) -> dict[str, Any]:
    blockers: list[str] = []
    notes = seed.get("notes")
    if not isinstance(notes, list) or not notes:
        blockers.append("seed_notes_empty")
        return {"notes_count": 0, "blockers": blockers}
    for index, note in enumerate(notes, start=1):
        if not isinstance(note, dict):
            blockers.append(f"seed_note_invalid:{index}")
            continue
        missing = {
            "seed_note_id",
            "note_type",
            "page_hint",
            "selected_text_anchor",
            "note_text",
            "user_tags",
            "expected_mechanism_relevance",
            "evidence_expectation",
        } - set(note)
        if missing:
            blockers.append(f"seed_note_missing_fields:{index}:{','.join(sorted(missing))}")
        tags = note.get("user_tags")
        if not isinstance(tags, list):
            blockers.append(f"seed_note_tags_invalid:{index}")
            continue
        tag_set = {str(tag) for tag in tags}
        missing_tags = REQUIRED_TAGS - tag_set
        if missing_tags:
            blockers.append(f"seed_note_missing_required_tags:{index}:{','.join(sorted(missing_tags))}")
        note_type = str(note.get("note_type") or "")
        if note_type not in NOTE_TYPE_TAGS:
            blockers.append(f"seed_note_type_invalid:{index}:{note_type}")
        if note_type not in tag_set:
            blockers.append(f"seed_note_type_tag_missing:{index}:{note_type}")
    return {"notes_count": len(notes), "blockers": blockers}


def _preflight(
    conn: sqlite3.Connection,
    *,
    require_document: bool,
    document_title: str,
    document_id: int | None,
) -> dict[str, Any]:
    blockers: list[str] = []
    warnings: list[str] = []
    tables = _tables(conn)
    counts = {table: _count_if_exists(conn, table) for table in COUNT_TABLES}
    integrity = _integrity_check(conn)
    if integrity != "ok":
        blockers.append("integrity_check_failed")
    for table in COUNT_TABLES:
        if table not in tables:
            blockers.append(f"required_table_missing:{table}")
    if "zotero_inspiration_notes" in tables:
        missing = REQUIRED_INSERT_COLUMNS - _columns(conn, "zotero_inspiration_notes")
        if require_document and missing:
            blockers.append(
                "zotero_inspiration_notes_missing_columns:" + ",".join(sorted(missing))
            )
    if "knowledge_chunks" in tables:
        missing = REQUIRED_CHUNK_COLUMNS - _columns(conn, "knowledge_chunks")
        if require_document and missing:
            blockers.append("knowledge_chunks_missing_columns:" + ",".join(sorted(missing)))

    candidates = _document_candidates(conn, document_title=document_title, document_id=document_id)
    target = None
    if require_document:
        if not candidates:
            blockers.append("target_document_not_found")
        elif len(candidates) > 1:
            blockers.append("ambiguous_document_candidates")
        else:
            target = _target_document_summary(conn, candidates[0])
            if not target.get("zotero_item_key") and not target.get("zotero_attachment_key"):
                warnings.append("zotero_keys_unavailable_using_null")
    return {
        "counts_before": counts,
        "integrity_check": integrity,
        "document_candidates": candidates,
        "target_document": target,
        "blockers": blockers,
        "warnings": warnings,
    }


def _document_candidates(
    conn: sqlite3.Connection,
    *,
    document_title: str,
    document_id: int | None,
) -> list[dict[str, Any]]:
    if not _table_exists(conn, "documents"):
        return []
    columns = _columns(conn, "documents")
    if not {"id", "title"} <= columns:
        return []
    selected = [
        "id",
        "title",
        _select_expr(columns, "document_type"),
        _select_expr(columns, "content_layer"),
        _select_expr(columns, "pdf_path"),
        _select_expr(columns, "zotero_key"),
    ]
    if document_id is not None:
        return _query_dicts(
            conn,
            f"SELECT {', '.join(selected)} FROM documents WHERE id = ?",
            (document_id,),
        )
    return _query_dicts(
        conn,
        f"""
        SELECT {', '.join(selected)}
        FROM documents
        WHERE lower(title) = lower(?)
           OR lower(title) LIKE lower(?)
           OR lower(title) LIKE lower(?)
           OR lower(title) LIKE lower(?)
        ORDER BY id
        """,
        (
            document_title,
            "%Real-Time Single Image and Video Super-Resolution%",
            "%Efficient Sub-Pixel Convolutional Neural Network%",
            "%ESPCN%",
        ),
    )


def _target_document_summary(conn: sqlite3.Connection, candidate: Mapping[str, Any]) -> dict[str, Any]:
    document_id = int(candidate["id"])
    chunk_count = _query_scalar(
        conn,
        "SELECT COUNT(*) FROM knowledge_chunks WHERE document_id = ?",
        (document_id,),
    )
    sources = _document_source_rows(conn, document_id)
    item_key = None
    attachment_key = None
    for row in sources:
        item_key = item_key or row.get("zotero_item_key")
        attachment_key = attachment_key or row.get("zotero_attachment_key")
    return {
        "document_id": document_id,
        "title": candidate.get("title"),
        "document_type": candidate.get("document_type"),
        "content_layer": candidate.get("content_layer"),
        "pdf_path": candidate.get("pdf_path"),
        "document_zotero_key": candidate.get("zotero_key"),
        "chunk_count": int(chunk_count or 0),
        "document_sources": sources,
        "zotero_item_key": item_key,
        "zotero_attachment_key": attachment_key,
    }


def _document_source_rows(conn: sqlite3.Connection, document_id: int) -> list[dict[str, Any]]:
    if not _table_exists(conn, "document_sources"):
        return []
    columns = _columns(conn, "document_sources")
    selected = [
        "id",
        "document_id",
        _select_expr(columns, "source_type"),
        _select_expr(columns, "zotero_item_key"),
        _select_expr(columns, "zotero_attachment_key"),
        _select_expr(columns, "zotero_select_uri"),
        _select_expr(columns, "zotero_open_pdf_uri"),
    ]
    return _query_dicts(
        conn,
        f"SELECT {', '.join(selected)} FROM document_sources WHERE document_id = ? ORDER BY id",
        (document_id,),
    )


def _tagged_seed_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    if not _table_exists(conn, "zotero_inspiration_notes"):
        return []
    columns = _columns(conn, "zotero_inspiration_notes")
    selected = [
        "id",
        _select_expr(columns, "server_note_id"),
        _select_expr(columns, "client_note_id"),
        _select_expr(columns, "source"),
        "user_tags_json",
        _select_expr(columns, "matched_document_id"),
        _select_expr(columns, "matched_chunk_id"),
        _select_expr(columns, "created_at"),
    ]
    candidates = _query_dicts(
        conn,
        f"""
        SELECT {', '.join(selected)}
        FROM zotero_inspiration_notes
        WHERE user_tags_json LIKE ?
        ORDER BY id
        """,
        (f"%{SEED_TAG}%",),
    )
    rows: list[dict[str, Any]] = []
    for row in candidates:
        tags = _json_list(row.get("user_tags_json"))
        if SEED_TAG not in tags:
            continue
        source = str(row.get("source") or "")
        row["user_tags"] = tags
        row["deletable"] = source == SEED_SOURCE
        rows.append(row)
    return rows


def _delete_old_seed_rows(conn: sqlite3.Connection, rows: list[Mapping[str, Any]]) -> int:
    ids = [int(row["id"]) for row in rows]
    if not ids:
        return 0
    placeholders = ", ".join(["?"] * len(ids))
    cursor = conn.execute(
        f"""
        DELETE FROM zotero_inspiration_notes
        WHERE id IN ({placeholders})
          AND source = ?
          AND user_tags_json LIKE ?
        """,
        tuple(ids) + (SEED_SOURCE, f"%{SEED_TAG}%"),
    )
    return int(cursor.rowcount)


def _build_note_records(
    conn: sqlite3.Connection,
    notes: list[Mapping[str, Any]],
    *,
    document_id: int,
    zotero_item_key: str | None,
    zotero_attachment_key: str | None,
) -> dict[str, Any]:
    chunks = _document_chunks(conn, document_id)
    records: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    warnings: list[str] = []
    for note in notes:
        seed_note_id = str(note["seed_note_id"])
        alignment = _find_anchor_alignment(chunks, str(note["selected_text_anchor"]))
        if alignment is None:
            blocked.append(
                {
                    "seed_note_id": seed_note_id,
                    "blocker": "selected_text_anchor_not_found",
                    "selected_text_anchor": note["selected_text_anchor"],
                }
            )
            continue
        selected_text = str(alignment["selected_text"]).strip()
        if not selected_text:
            blocked.append(
                {
                    "seed_note_id": seed_note_id,
                    "blocker": "selected_text_empty",
                    "selected_text_anchor": note["selected_text_anchor"],
                }
            )
            continue
        object_resolution = _resolve_objects(
            conn,
            document_id,
            OBJECT_TERMS_BY_SEED_NOTE_ID.get(seed_note_id, ("ESPCN", "Super-Resolution")),
        )
        warnings.extend(object_resolution["warnings"])
        primary = alignment["primary_chunk"]
        chunk_ids = [int(chunk["id"]) for chunk in alignment["matched_chunks"]]
        alignment_warnings = [
            BASE_ALIGNMENT_WARNING,
            *alignment["warnings"],
            *object_resolution["warnings"],
        ]
        note_number = seed_note_id.rsplit("_", 1)[-1]
        record = {
            "server_note_id": f"zinsp_synth_{seed_note_id}",
            "client_note_id": f"zinsp_client_synth_{seed_note_id}",
            "source": SEED_SOURCE,
            "zotero_item_key": zotero_item_key,
            "zotero_attachment_key": zotero_attachment_key,
            "zotero_annotation_key": None,
            "pdf_page": _optional_int(primary.get("pdf_page_start")),
            "page_label": str(_optional_int(primary.get("pdf_page_start")) or note["page_hint"]),
            "selected_text": selected_text,
            "selected_text_hash": _selected_text_hash(selected_text),
            "note_text": note["note_text"],
            "user_tags_json": json.dumps(list(note["user_tags"]), ensure_ascii=False),
            "selection_type": "paragraph",
            "context_before": None,
            "context_after": None,
            "bbox_json": None,
            "matched_document_id": document_id,
            "matched_chunk_id": int(primary["id"]),
            "matched_object_ids_json": json.dumps(object_resolution["object_ids"]),
            "sync_status": "synced",
            "match_status": "matched",
            "review_status": "imported",
            "mechanism_status": "not_generated",
            "created_at": CREATED_AT,
            "updated_at": CREATED_AT,
            "received_at": CREATED_AT,
            "matched_chunk_ids_json": json.dumps(chunk_ids),
            "evidence_alignment_status": alignment["evidence_alignment_status"],
            "alignment_confidence": alignment["alignment_confidence"],
            "alignment_method": ALIGNMENT_METHOD,
            "alignment_warnings_json": json.dumps(
                list(dict.fromkeys(alignment_warnings)),
                ensure_ascii=False,
            ),
        }
        records.append(record)
        summaries.append(
            {
                "seed_note_id": seed_note_id,
                "server_note_id": record["server_note_id"],
                "client_note_id": record["client_note_id"],
                "note_number": note_number,
                "note_type": note["note_type"],
                "page_hint": note["page_hint"],
                "pdf_page": record["pdf_page"],
                "selected_text_length": len(selected_text),
                "matched_document_id": document_id,
                "matched_chunk_id": record["matched_chunk_id"],
                "matched_chunk_ids": chunk_ids,
                "evidence_alignment_status": alignment["evidence_alignment_status"],
                "alignment_confidence": alignment["alignment_confidence"],
                "matched_object_ids": object_resolution["object_ids"],
                "unresolved_object_terms": object_resolution["unresolved_terms"],
                "expected_mechanism_relevance": note["expected_mechanism_relevance"],
                "evidence_expectation": note["evidence_expectation"],
                "source": SEED_SOURCE,
                "mechanism_status": "not_generated",
            }
        )
    return {
        "records": records,
        "note_summaries": summaries,
        "blocked_notes": blocked,
        "warnings": list(dict.fromkeys(warnings)),
    }


def _document_chunks(conn: sqlite3.Connection, document_id: int) -> list[dict[str, Any]]:
    columns = _columns(conn, "knowledge_chunks")
    selected = [
        "id",
        "document_id",
        _select_expr(columns, "chunk_index"),
        _select_expr(columns, "heading_path"),
        "chunk_text",
        _select_expr(columns, "pdf_page_start"),
        _select_expr(columns, "pdf_page_end"),
    ]
    return _query_dicts(
        conn,
        f"SELECT {', '.join(selected)} FROM knowledge_chunks WHERE document_id = ? ORDER BY id",
        (document_id,),
    )


def _find_anchor_alignment(
    chunks: list[dict[str, Any]],
    anchor: str,
) -> dict[str, Any] | None:
    canonical_anchor = _canonical_match_text(anchor)
    for index, chunk in enumerate(chunks):
        if canonical_anchor in _canonical_match_text(str(chunk.get("chunk_text") or "")):
            selected_chunks = _selection_chunks(chunks, index)
            evidence_status = "matched" if len(selected_chunks) == 1 else "span_matched"
            warnings = []
            if len(selected_chunks) > 1:
                warnings.append("selected_text_expanded_to_neighbor_chunks_for_context")
            return {
                "primary_chunk": chunk,
                "matched_chunks": selected_chunks,
                "selected_text": _joined_chunk_text(selected_chunks),
                "evidence_alignment_status": evidence_status,
                "alignment_confidence": "high",
                "warnings": warnings,
            }
    for index in range(len(chunks) - 1):
        first = _canonical_match_text(str(chunks[index].get("chunk_text") or ""))
        second = _canonical_match_text(str(chunks[index + 1].get("chunk_text") or ""))
        if _anchor_spans_pair(canonical_anchor, first, second):
            return {
                "primary_chunk": chunks[index],
                "matched_chunks": [chunks[index], chunks[index + 1]],
                "selected_text": _joined_chunk_text([chunks[index], chunks[index + 1]]),
                "evidence_alignment_status": "span_matched",
                "alignment_confidence": "medium",
                "warnings": ["anchor_matched_across_adjacent_chunks"],
            }
    return None


def _anchor_spans_pair(anchor: str, first: str, second: str) -> bool:
    words = anchor.split()
    if len(words) < 6:
        return False
    for split in range(3, len(words) - 2):
        prefix = " ".join(words[:split])
        suffix = " ".join(words[split:])
        if len(prefix) < 20 or len(suffix) < 20:
            continue
        if prefix in first and suffix in second:
            return True
    return False


def _selection_chunks(chunks: list[dict[str, Any]], anchor_index: int) -> list[dict[str, Any]]:
    selected = [chunks[anchor_index]]
    if len(_joined_chunk_text(selected)) >= 300:
        return selected
    if anchor_index > 0:
        selected.insert(0, chunks[anchor_index - 1])
    if len(_joined_chunk_text(selected)) >= 300:
        return selected
    if anchor_index + 1 < len(chunks):
        selected.append(chunks[anchor_index + 1])
    return selected


def _joined_chunk_text(chunks: Iterable[Mapping[str, Any]]) -> str:
    text = "\n\n".join(str(chunk.get("chunk_text") or "").strip() for chunk in chunks)
    if len(text) <= 900:
        return text
    return text[:900].rstrip()


def _resolve_objects(
    conn: sqlite3.Connection,
    document_id: int,
    terms: tuple[str, ...],
) -> dict[str, Any]:
    if not _table_exists(conn, "object_candidates"):
        return {
            "object_ids": [],
            "unresolved_terms": list(terms),
            "warnings": ["object_metadata_unresolved"],
        }
    columns = _columns(conn, "object_candidates")
    required = {"id", "document_id", "object_name", "review_status"}
    if not required <= columns:
        return {
            "object_ids": [],
            "unresolved_terms": list(terms),
            "warnings": ["object_metadata_unresolved"],
        }
    selected = [
        "id",
        "document_id",
        "object_name",
        _select_expr(columns, "object_key"),
        _select_expr(columns, "aliases_json"),
        _select_expr(columns, "review_status"),
        _select_expr(columns, "status"),
    ]
    rows = _query_dicts(
        conn,
        f"SELECT {', '.join(selected)} FROM object_candidates WHERE document_id = ?",
        (document_id,),
    )
    object_ids: list[int] = []
    unresolved: list[str] = []
    for term in terms:
        matched = _match_object_term(rows, term)
        if matched is None:
            unresolved.append(term)
            continue
        object_id = int(matched["id"])
        if object_id not in object_ids:
            object_ids.append(object_id)
    warnings: list[str] = []
    if unresolved:
        warnings.append("object_metadata_unresolved")
        warnings.extend(f"object_metadata_unresolved:{term}" for term in unresolved)
    return {
        "object_ids": object_ids,
        "unresolved_terms": unresolved,
        "warnings": warnings,
    }


def _match_object_term(rows: list[dict[str, Any]], term: str) -> dict[str, Any] | None:
    normalized = _object_key(term)
    for row in rows:
        if _public_review_status(row.get("review_status")) != "approved":
            continue
        if str(row.get("status") or "").strip().lower() == "deprecated":
            continue
        aliases = _json_list(row.get("aliases_json"))
        values = [str(row.get("object_name") or ""), str(row.get("object_key") or ""), *aliases]
        if any(_object_key(value) == normalized for value in values):
            return row
    return None


def _insert_records(conn: sqlite3.Connection, records: list[Mapping[str, Any]]) -> int:
    placeholders = ", ".join([f":{column}" for column in INSERT_COLUMNS])
    columns = ", ".join(INSERT_COLUMNS)
    inserted = 0
    for record in records:
        conn.execute(
            f"INSERT INTO zotero_inspiration_notes ({columns}) VALUES ({placeholders})",
            {column: record.get(column) for column in INSERT_COLUMNS},
        )
        inserted += 1
    return inserted


def _postflight(
    conn: sqlite3.Connection,
    *,
    schema_before: Mapping[str, Any],
    baseline_counts: Mapping[str, int],
    document_id: int | None,
) -> dict[str, Any]:
    counts_after = {table: _count_if_exists(conn, table) for table in COUNT_TABLES}
    return {
        "counts_after": counts_after,
        "integrity_check": _integrity_check(conn),
        "tagged_note_count": _tagged_note_count(conn, document_id=document_id),
        "mechanism_draft_candidates_unchanged": counts_after.get("mechanism_draft_candidates")
        == baseline_counts.get("mechanism_draft_candidates"),
        "knowledge_chunks_unchanged": counts_after.get("knowledge_chunks")
        == baseline_counts.get("knowledge_chunks"),
        "schema_changed": _schema_snapshot(conn) != dict(schema_before),
    }


def _apply_postflight_guards(report: dict[str, Any]) -> None:
    postflight = report.get("postflight") or {}
    if postflight.get("integrity_check") != "ok":
        report["status"] = "BLOCKED"
        report["blockers"].append("integrity_check_failed_postflight")
    if postflight.get("schema_changed"):
        report["status"] = "BLOCKED"
        report["blockers"].append("schema_changed")
    if not postflight.get("mechanism_draft_candidates_unchanged", True):
        report["status"] = "BLOCKED"
        report["blockers"].append("mechanism_draft_candidates_count_changed")
    if not postflight.get("knowledge_chunks_unchanged", True):
        report["status"] = "BLOCKED"
        report["blockers"].append("knowledge_chunks_count_changed")


def _tagged_note_count(conn: sqlite3.Connection, *, document_id: int | None) -> int:
    rows = _tagged_seed_rows(conn)
    if document_id is None:
        return len(rows)
    return sum(1 for row in rows if row.get("matched_document_id") == document_id)


def _note_type_distribution(notes: list[Mapping[str, Any]]) -> dict[str, int]:
    distribution = {note_type: 0 for note_type in sorted(NOTE_TYPE_TAGS)}
    for note in notes:
        note_type = str(note.get("note_type") or "")
        if note_type in distribution:
            distribution[note_type] += 1
    return distribution


def _chunk_alignment_summary(notes: list[Mapping[str, Any]]) -> dict[str, Any]:
    by_status: dict[str, int] = {}
    chunk_ids: list[list[int]] = []
    for note in notes:
        status = str(note.get("evidence_alignment_status") or "unknown")
        by_status[status] = by_status.get(status, 0) + 1
        chunk_ids.append(list(note.get("matched_chunk_ids") or []))
    return {
        "aligned_notes": len(notes),
        "by_status": by_status,
        "matched_chunk_ids": chunk_ids,
    }


def _object_resolution_summary(notes: list[Mapping[str, Any]]) -> dict[str, Any]:
    resolved_notes = sum(1 for note in notes if note.get("matched_object_ids"))
    unresolved_terms = sorted(
        {
            term
            for note in notes
            for term in list(note.get("unresolved_object_terms") or [])
        }
    )
    return {
        "notes_with_objects": resolved_notes,
        "notes_without_objects": len(notes) - resolved_notes,
        "unresolved_terms": unresolved_terms,
    }


def _schema_snapshot(conn: sqlite3.Connection) -> dict[str, Any]:
    return {
        "sqlite_master": _query_dicts(
            conn,
            """
            SELECT type, name, tbl_name, sql
            FROM sqlite_master
            WHERE type IN ('table', 'index', 'trigger', 'view')
            ORDER BY type, name
            """,
        )
    }


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()
        is not None
    )


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _select_expr(columns: set[str], column: str) -> str:
    return column if column in columns else f"NULL AS {column}"


def _query_dicts(
    conn: sqlite3.Connection,
    sql: str,
    params: tuple[Any, ...] = (),
) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def _query_scalar(
    conn: sqlite3.Connection,
    sql: str,
    params: tuple[Any, ...] = (),
) -> Any:
    row = conn.execute(sql, params).fetchone()
    return None if row is None else row[0]


def _count_if_exists(conn: sqlite3.Connection, table: str) -> int:
    if not _table_exists(conn, table):
        return 0
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def _integrity_check(conn: sqlite3.Connection) -> str:
    row = conn.execute("PRAGMA integrity_check").fetchone()
    return str(row[0] if row else "")


def _json_list(value: Any) -> list[str]:
    if value is None:
        return []
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed]


def _canonical_match_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    normalized = normalized.replace("r²", "r2")
    normalized = normalized.replace("×", "x")
    normalized = normalized.replace("·", " ")
    normalized = re.sub(r"(?<=[A-Za-z])-\s+(?=[A-Za-z])", "", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip().lower()


def _object_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    normalized = normalized.lower()
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def _public_review_status(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"accepted", "approved", "edited"}:
        return "approved"
    return normalized or "unknown"


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _selected_text_hash(selected_text: str) -> str:
    normalized = unicodedata.normalize("NFC", selected_text)
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    normalized = re.sub(r"[ \t]+", " ", normalized).strip()
    return "sha256:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _sqlite_uri(db_path: Path, *, mode: str) -> str:
    return f"file:{db_path.resolve().as_posix()}?mode={mode}"


def _blocked_message(blockers: list[str]) -> str:
    if "target_document_not_found" in blockers:
        return "用户需要先在前端导入该 PDF"
    if "ambiguous_document_candidates" in blockers:
        return "找到多个候选 document，请传入 --document-id"
    return "seed preflight blocked"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prepare or apply ESPCN synthetic frontend acceptance notes."
    )
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--seed-json", type=Path, default=DEFAULT_SEED_PATH)
    parser.add_argument("--document-title", default=DEFAULT_DOCUMENT_TITLE)
    parser.add_argument("--document-id", type=int, default=None)
    parser.add_argument("--limit", type=int, default=None)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Report only; default.")
    mode.add_argument("--apply", action="store_true", help="Delete old seed rows and insert notes.")
    parser.add_argument("--cleanup-only", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = build_seed_report(
        args.db_path,
        seed_json=args.seed_json,
        apply=args.apply,
        cleanup_only=args.cleanup_only,
        document_title=args.document_title,
        document_id=args.document_id,
        limit=args.limit,
    )
    output = json.dumps(report, ensure_ascii=False, indent=2)
    _print(output + "\n")
    return 0


def _print(value: str) -> None:
    buffer = getattr(sys.stdout, "buffer", None)
    if buffer is None:
        print(value, end="")
        return
    buffer.write(value.encode("utf-8"))


if __name__ == "__main__":
    raise SystemExit(main())
