"""Chapter review loading responsibilities."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from app.core.database import (
    connect_existing_readwrite_sqlite,
    connect_readonly_sqlite,
    connect_sqlite,
)
from app.core.paths import DEFAULT_DB_PATH
from app.services.chapter_note_correction_prompt_service import (
    FORBIDDEN_REVIEW_KEYS,
    ChapterNoteCorrectionPromptError,
    build_chapter_note_correction_prompt_package,
    build_chapter_note_correction_canary_subscope_package,
    build_chapter_note_correction_scoped_package,
    note_correction_dry_run_safety_flags,
    validate_chapter_note_correction_review,
)
from app.services.unit_note_object_processing_service import table_exists

from .contracts import (
    NOTE_CORRECTION_REVIEW_TABLE,
    NOTE_CORRECTION_REVIEW_ITEM_TABLE,
    NOTE_CLASSIFICATION_REVIEW_TABLE,
    NOTE_CLASSIFICATION_REVIEW_ITEM_TABLE,
    NOTE_CORRECTION_SAVE_CONTEXT,
    NOTE_CLASSIFICATION_SAVE_CONTEXT,
    OBJECT_CANDIDATE_DRAFT_SAVE_CONTEXT,
    OBJECT_CANDIDATE_HUMAN_REVIEW_SAVE_CONTEXT,
    NOTE_CORRECTION_REVIEW_SAVE_SCHEMA_VERSION,
    NOTE_CORRECTION_HUMAN_AUDIT_SCHEMA_VERSION,
    NOTE_CLASSIFICATION_REVIEW_SAVE_SCHEMA_VERSION,
    OBJECT_CANDIDATE_DRAFT_SAVE_SCHEMA_VERSION,
    OBJECT_CANDIDATE_HUMAN_REVIEW_SCHEMA_VERSION,
    PRODUCTION_DB_WRITE_ENABLED,
    PRODUCTION_REVIEW_SAVE_CANARY_ENV,
    PRODUCTION_REVIEW_SAVE_SECTION_ENV,
    PRODUCTION_REVIEW_SAVE_SECTION84_PN68_ENV,
    PRODUCTION_REVIEW_SAVE_SECTION_TARGET_ENV,
    PRODUCTION_NOTE_CLASSIFICATION_SAVE_ENV,
    PRODUCTION_OBJECT_CANDIDATE_DRAFT_SAVE_ENV,
    PRODUCTION_OBJECT_CANDIDATE_HUMAN_REVIEW_SAVE_ENV,
    OBJECT_CANDIDATE_DRAFT_REVIEW_TABLE,
    OBJECT_CANDIDATE_DRAFT_REVIEW_ITEM_TABLE,
    OBJECT_CANDIDATE_HUMAN_REVIEW_TABLE,
    OBJECT_CANDIDATE_HUMAN_REVIEW_ITEM_TABLE,
    PRODUCTION_REVIEW_CANARY_WRITE_TABLES,
    PRODUCTION_NOTE_CLASSIFICATION_WRITE_TABLES,
    PRODUCTION_OBJECT_CANDIDATE_DRAFT_WRITE_TABLES,
    PRODUCTION_OBJECT_CANDIDATE_HUMAN_REVIEW_WRITE_TABLES,
    PRODUCTION_REVIEW_SECTION_DOCUMENT_ID,
    PRODUCTION_REVIEW_SECTION_CHAPTER_ID,
    PRODUCTION_REVIEW_SECTION_ALLOWED_SCOPES,
    PRODUCTION_REVIEW_SECTION_DEFERRED_SCOPES,
    PRODUCTION_REVIEW_SECTION84_PN68_SCOPE_ID,
    PRODUCTION_REVIEW_SECTION84_PN68_EXPECTED_COUNT,
    PRODUCTION_REVIEW_SECTION84_PN68_ZOTERO_KEY,
    PRODUCTION_REVIEW_SECTION84_PN68_SERVER_NOTE_ID,
    PRODUCTION_REVIEW_SECTION84_PN68_ALLOWED_STATUSES,
    PRODUCTION_REVIEW_SECTION84_PN68_REQUIRED_WARNINGS,
    PRODUCTION_OBJECT_CANDIDATE_DRAFT_DOCUMENT_ID,
    PRODUCTION_OBJECT_CANDIDATE_DRAFT_CHAPTER_ID,
    PRODUCTION_OBJECT_CANDIDATE_DRAFT_SOURCE_REVIEW_ID,
    PRODUCTION_OBJECT_CANDIDATE_DRAFT_EXPECTED_COUNT,
    PRODUCTION_OBJECT_CANDIDATE_DRAFT_EXPECTED_QUARANTINED_COUNT,
    PRODUCTION_OBJECT_CANDIDATE_DRAFT_REVIEW_ID,
    PRODUCTION_OBJECT_CANDIDATE_HUMAN_REVIEW_ID,
    MERGED_NOTE_CORRECTION_SECTION_ORDER,
    NOTE_CLASSIFICATION_LABEL_ORDER,
    NOTE_CLASSIFICATION_LABELS,
    NOTE_CLASSIFICATION_MANUAL_CONFIDENCE_ORDER,
    NOTE_CLASSIFICATION_MANUAL_CONFIDENCES,
    OBJECT_CANDIDATE_DRY_RUN_TYPE_ORDER,
    OBJECT_CANDIDATE_DRY_RUN_TYPES,
)

from .normalization import (
    _candidate_order_for_item,
    _ignored_review_item_summary,
    _int_or_none,
    _item_key,
    _loads,
    _normalized_item_from_saved_row,
)

from .persistence import (
    _connect_ro_existing,
    _latest_object_candidate_draft_review_row,
    _latest_object_candidate_human_review_row,
    _latest_saved_classification_review_row,
    _latest_saved_correction_review_row,
    _note_classification_review_schema_ready,
    _note_correction_review_schema_ready,
    _object_candidate_draft_review_schema_ready,
    _object_candidate_human_review_schema_ready,
)

from .safety import (
    review_pipeline_safety_flags,
)

def build_note_correction_production_db_snapshot(
    *,
    research_db_path: str | Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    db_path = Path(research_db_path)
    counts = {
        NOTE_CORRECTION_REVIEW_TABLE: 0,
        NOTE_CORRECTION_REVIEW_ITEM_TABLE: 0,
    }
    conn = _connect_ro_existing(db_path)
    try:
        for table in counts:
            if table_exists(conn, table):
                counts[table] = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    finally:
        conn.close()
    flags = review_pipeline_safety_flags()
    return {
        "status": "ok",
        "mode": "note_correction_review_production_db_snapshot",
        "db_path": str(db_path),
        "db_hash_sha256": hashlib.sha256(db_path.read_bytes()).hexdigest(),
        "counts": counts,
        "canary_snapshot_only": True,
        "real_save_api_called": False,
        "safety_flags": flags,
        **flags,
    }


def load_saved_object_candidate_draft_review(
    *,
    research_db_path: str | Path = DEFAULT_DB_PATH,
    document_id: int,
    chapter_id: int,
    source_classification_review_id: str | None = None,
) -> dict[str, Any] | None:
    db_path = Path(research_db_path)
    conn = _connect_ro_existing(db_path)
    try:
        conn.row_factory = sqlite3.Row
        if not _object_candidate_draft_review_schema_ready(conn):
            return None
        row = _latest_object_candidate_draft_review_row(
            conn,
            document_id=document_id,
            chapter_id=chapter_id,
            source_classification_review_id=source_classification_review_id,
        )
        if not row:
            return None
        item_rows = conn.execute(
            f"""
            SELECT *
            FROM {OBJECT_CANDIDATE_DRAFT_REVIEW_ITEM_TABLE}
            WHERE review_id = ?
            ORDER BY id
            """,
            (row["review_id"],),
        ).fetchall()
        items = []
        for item in item_rows:
            item_dict = dict(item)
            item_dict["source_server_note_ids"] = _loads(item_dict.get("source_server_note_ids_json"), [])
            item_dict["source_labels"] = _loads(item_dict.get("source_labels_json"), [])
            item_dict["evidence_chunk_ids"] = _loads(item_dict.get("evidence_chunk_ids_json"), [])
            item_dict["page_labels"] = _loads(item_dict.get("page_labels_json"), [])
            items.append(item_dict)
        return {
            "status": row["review_status"],
            "review_id": row["review_id"],
            "object_candidate_review_id": row["review_id"],
            "batch_id": row["review_id"],
            "review_type": "object_candidate_draft_review",
            "review_mode": row["review_mode"],
            "document_id": row["document_id"],
            "chapter_id": row["chapter_id"],
            "source_classification_review_id": row["source_classification_review_id"],
            "source_package_hash": row["source_package_hash"],
            "dry_run_package_hash": row["dry_run_package_hash"],
            "candidate_count": row["candidate_count"],
            "saved_candidate_count": len(items),
            "quarantined_count": row["quarantined_count"],
            "pn68_quarantined": bool(row["pn68_quarantined"]),
            "ready_for_object_human_review": row["review_status"] == "pending_human_review",
            "approved_objects_created": bool(row["approved_objects_created"]),
            "object_candidates_generated": bool(row["object_candidates_generated"]),
            "relation_generated": bool(row["relation_generated"]),
            "mechanism_generated": bool(row["mechanism_generated"]),
            "zotero_write_performed": bool(row["zotero_write_performed"]),
            "vector_write_performed": bool(row["vector_write_performed"]),
            "llm_called": bool(row["llm_called"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "items": items,
            "preview_items": items[:6],
        }
    finally:
        conn.close()


def load_saved_object_candidate_human_review(
    *,
    research_db_path: str | Path = DEFAULT_DB_PATH,
    document_id: int,
    chapter_id: int,
    source_draft_review_id: str | None = None,
) -> dict[str, Any] | None:
    db_path = Path(research_db_path)
    conn = _connect_ro_existing(db_path)
    try:
        conn.row_factory = sqlite3.Row
        if not _object_candidate_human_review_schema_ready(conn):
            return None
        row = _latest_object_candidate_human_review_row(
            conn,
            document_id=document_id,
            chapter_id=chapter_id,
            source_draft_review_id=source_draft_review_id,
        )
        if not row:
            return None
        item_rows = conn.execute(
            f"""
            SELECT *
            FROM {OBJECT_CANDIDATE_HUMAN_REVIEW_ITEM_TABLE}
            WHERE human_review_id = ?
            ORDER BY id
            """,
            (row["human_review_id"],),
        ).fetchall()
        items = []
        for item in item_rows:
            item_dict = dict(item)
            item_dict["source_server_note_ids"] = _loads(item_dict.get("source_server_note_ids_json"), [])
            item_dict["source_labels"] = _loads(item_dict.get("source_labels_json"), [])
            item_dict["evidence_chunk_ids"] = _loads(item_dict.get("evidence_chunk_ids_json"), [])
            item_dict["page_labels"] = _loads(item_dict.get("page_labels_json"), [])
            items.append(item_dict)
        return {
            "status": row["review_status"],
            "human_review_id": row["human_review_id"],
            "object_candidate_human_review_id": row["human_review_id"],
            "document_id": row["document_id"],
            "chapter_id": row["chapter_id"],
            "source_draft_review_id": row["source_draft_review_id"],
            "source_classification_review_id": row["source_classification_review_id"],
            "candidate_count": row["candidate_count"],
            "saved_item_count": len(items),
            "approved_count": row["approved_count"],
            "rejected_count": row["rejected_count"],
            "edited_count": row["edited_count"],
            "merged_count": row["merged_count"],
            "pending_count": row["pending_count"],
            "pn68_quarantined": bool(row["pn68_quarantined"]),
            "ready_for_relation_dry_run": int(row["approved_count"] or 0) > 0,
            "relation_mechanism_locked": True,
            "approved_objects_created": bool(row["approved_objects_created"]),
            "object_candidates_generated": bool(row["object_candidates_generated"]),
            "relation_generated": bool(row["relation_generated"]),
            "mechanism_generated": bool(row["mechanism_generated"]),
            "zotero_write_performed": bool(row["zotero_write_performed"]),
            "vector_write_performed": bool(row["vector_write_performed"]),
            "llm_called": bool(row["llm_called"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "items": items,
            "preview_items": items[:6],
        }
    finally:
        conn.close()


def load_saved_note_correction_review(
    *,
    research_db_path: str | Path = DEFAULT_DB_PATH,
    document_id: int,
    chapter_id: int,
) -> dict[str, Any] | None:
    db_path = Path(research_db_path)
    conn = _connect_ro_existing(db_path)
    try:
        conn.row_factory = sqlite3.Row
        if not _note_correction_review_schema_ready(conn):
            return None
        row = _latest_saved_correction_review_row(
            conn,
            document_id=document_id,
            chapter_id=chapter_id,
        )
        if not row:
            return None
        item_rows = conn.execute(
            f"""
            SELECT *
            FROM {NOTE_CORRECTION_REVIEW_ITEM_TABLE}
            WHERE review_id = ?
            ORDER BY rowid
            """,
            (row["review_id"],),
        ).fetchall()
        return {
            **dict(row),
            "review_json": _loads(row["normalized_review_json"], {}),
            "normalized_items": (_loads(row["normalized_review_json"], {}).get("note_correction_review") or {}).get("items") or [],
            "stats": _loads(row["review_summary_json"], {}),
            "completeness": _loads(row["completeness_json"], {}),
            "review_items": [dict(item) for item in item_rows],
        }
    finally:
        conn.close()


def load_merged_saved_note_correction_review(
    *,
    research_db_path: str | Path = DEFAULT_DB_PATH,
    document_id: int,
    chapter_id: int,
    correction_package: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Read all saved correction reviews and merge one item per server_note_id.

    The legacy loader intentionally returns the latest saved review row. The
    classification package needs chapter-level state after section-by-section
    saves, so it must merge saved section reviews while keeping canary reviews
    as audit trace only.
    """
    db_path = Path(research_db_path)
    package = correction_package or build_chapter_note_correction_prompt_package(
        research_db_path=research_db_path,
        document_id=document_id,
        chapter_id=chapter_id,
    )
    summary = package.get("notes_summary") or {}
    expected_count = int(summary.get("correction_candidate_count") or 0)
    candidate_order = {
        _item_key(candidate): index
        for index, candidate in enumerate(package.get("correction_candidates") or [])
        if _item_key(candidate)
    }
    expected_keys = set(candidate_order)
    conn = _connect_ro_existing(db_path)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        if not _note_correction_review_schema_ready(conn):
            return None
        review_rows = conn.execute(
            f"""
            SELECT *
            FROM {NOTE_CORRECTION_REVIEW_TABLE}
            WHERE document_id = ?
              AND chapter_id = ?
              AND review_status = 'saved'
            ORDER BY updated_at DESC, created_at DESC
            """,
            (document_id, chapter_id),
        ).fetchall()
        if not review_rows:
            return None
        review_ids = [str(row["review_id"]) for row in review_rows]
        placeholders = ", ".join("?" for _ in review_ids)
        item_rows = conn.execute(
            f"""
            SELECT items.*, items.rowid AS item_rowid,
                   reviews.review_mode AS source_review_mode,
                   reviews.scope_id AS source_scope_id,
                   reviews.updated_at AS source_review_updated_at,
                   reviews.created_at AS source_review_created_at
            FROM {NOTE_CORRECTION_REVIEW_ITEM_TABLE} AS items
            JOIN {NOTE_CORRECTION_REVIEW_TABLE} AS reviews
              ON reviews.review_id = items.review_id
            WHERE items.review_id IN ({placeholders})
            ORDER BY reviews.updated_at DESC, items.rowid ASC
            """,
            review_ids,
        ).fetchall()
    finally:
        conn.close()

    review_payloads = {
        str(row["review_id"]): _saved_review_payload(row)
        for row in review_rows
    }
    item_rows_by_review: dict[str, list[dict[str, Any]]] = {}
    for row in item_rows:
        item = dict(row)
        item_rows_by_review.setdefault(str(item["review_id"]), []).append(item)

    review_rows_by_id = {str(row["review_id"]): row for row in review_rows}
    section_review_ids = [
        str(row["review_id"])
        for row in sorted(review_rows, key=_saved_review_section_sort_key)
        if row["review_mode"] == "section_scoped"
    ]
    canary_review_ids = [
        str(row["review_id"])
        for row in sorted(review_rows, key=_saved_review_section_sort_key)
        if row["review_mode"] == "canary_subscope"
    ]
    other_review_ids = [
        str(row["review_id"])
        for row in sorted(review_rows, key=_saved_review_section_sort_key)
        if row["review_mode"] not in {"section_scoped", "canary_subscope"}
    ]
    ordered_review_ids = section_review_ids + other_review_ids + canary_review_ids

    merged_items_by_key: dict[str, dict[str, Any]] = {}
    merged_normalized_by_key: dict[str, dict[str, Any]] = {}
    duplicate_review_items_ignored: list[dict[str, Any]] = []
    canary_items_shadowed: list[dict[str, Any]] = []

    for review_id in ordered_review_ids:
        review_row = review_rows_by_id[review_id]
        payload = review_payloads.get(review_id) or {}
        normalized_by_key = payload.get("normalized_by_key") or {}
        for item in sorted(
            item_rows_by_review.get(review_id, []),
            key=lambda value: (
                _section_index(value.get("source_scope_id")),
                _candidate_order_for_item(value, candidate_order),
                _int_or_none(value.get("page")) if _int_or_none(value.get("page")) is not None else 10**9,
                _int_or_none(value.get("item_rowid")) or 10**9,
            ),
        ):
            key = _item_key(item)
            if not key or (expected_keys and key not in expected_keys):
                duplicate_review_items_ignored.append(_ignored_review_item_summary(item, reason="not_in_correction_package"))
                continue
            normalized_item = dict(normalized_by_key.get(key) or _normalized_item_from_saved_row(item))
            normalized_item.setdefault("server_note_id", item.get("server_note_id"))
            normalized_item.setdefault("client_note_id", item.get("client_note_id"))
            normalized_item.setdefault("zotero_annotation_key", item.get("zotero_annotation_key"))
            if key in merged_items_by_key:
                ignored = _ignored_review_item_summary(item, reason="duplicate_shadowed_by_prior_review")
                if review_row["review_mode"] == "canary_subscope":
                    canary_items_shadowed.append(ignored)
                else:
                    duplicate_review_items_ignored.append(ignored)
                continue
            enriched_item = {
                **item,
                "source_review_id": review_id,
                "source_review_mode": review_row["review_mode"],
                "source_section_id": review_row["scope_id"] if review_row["review_mode"] == "section_scoped" else None,
            }
            merged_items_by_key[key] = enriched_item
            merged_normalized_by_key[key] = normalized_item

    merged_keys = sorted(
        merged_items_by_key,
        key=lambda key: (
            _section_index(merged_items_by_key[key].get("source_section_id")),
            candidate_order.get(key, 10**9),
            _int_or_none(merged_items_by_key[key].get("page")) if _int_or_none(merged_items_by_key[key].get("page")) is not None else 10**9,
            key,
        ),
    )
    review_items = [merged_items_by_key[key] for key in merged_keys]
    normalized_items = [merged_normalized_by_key[key] for key in merged_keys]
    confirmed_count = sum(1 for item in review_items if bool(item.get("confirmed_by_user")))
    needs_followup_count = sum(1 for item in review_items if item.get("human_action") == "needs_followup")
    final_note_text_count = sum(1 for item in review_items if str(item.get("final_note_text") or "").strip())
    source_section_ids = [
        str(row["scope_id"])
        for row in sorted(review_rows, key=_saved_review_section_sort_key)
        if row["review_mode"] == "section_scoped" and str(row["scope_id"] or "").strip()
    ]
    pn68_items = [item for item in review_items if item.get("zotero_annotation_key") == PRODUCTION_REVIEW_SECTION84_PN68_ZOTERO_KEY]
    pn68_item = pn68_items[0] if pn68_items else None
    pn68_status = {
        "status": "saved" if len(pn68_items) == 1 else ("missing" if not pn68_items else "duplicate"),
        "included_count": len(pn68_items),
        "included_once": len(pn68_items) == 1,
        "warning_preserved": bool(str((pn68_item or {}).get("ai_reviewer_warning") or "").strip()),
        "server_note_id": (pn68_item or {}).get("server_note_id"),
        "zotero_annotation_key": (pn68_item or {}).get("zotero_annotation_key"),
        "correction_status": (pn68_item or {}).get("ai_correction_status"),
        "issue_type": (pn68_item or {}).get("ai_issue_type"),
        "evidence_support": (pn68_item or {}).get("ai_evidence_support"),
        "reviewer_warning": (pn68_item or {}).get("ai_reviewer_warning"),
        "human_action": (pn68_item or {}).get("human_action"),
        "writeback_intent": (pn68_item or {}).get("writeback_intent"),
    }
    ready_for_classification = (
        expected_count > 0
        and len(review_items) == expected_count
        and len({str(item.get("server_note_id") or "").strip() for item in review_items if item.get("server_note_id")}) == expected_count
        and confirmed_count == expected_count
        and needs_followup_count == 0
        and pn68_status["status"] != "duplicate"
    )
    source_review_ids = [str(row["review_id"]) for row in sorted(review_rows, key=_saved_review_section_sort_key)]
    latest = review_rows[0]
    merged_metadata = {
        "status": "saved" if ready_for_classification else "partial",
        "mode": "merged_saved_note_correction_review",
        "document_id": document_id,
        "chapter_id": chapter_id,
        "expected_count": expected_count,
        "item_count": len(review_items),
        "unique_server_note_ids": len({
            str(item.get("server_note_id") or "").strip()
            for item in review_items
            if str(item.get("server_note_id") or "").strip()
        }),
        "source_review_count": len(review_rows),
        "source_section_review_count": len(section_review_ids),
        "canary_audit_count": len(canary_review_ids),
        "source_review_ids": source_review_ids,
        "source_section_ids": source_section_ids,
        "duplicate_review_items_ignored": duplicate_review_items_ignored,
        "canary_items_shadowed": canary_items_shadowed,
        "pn68_status": pn68_status,
        "confirmed_count": confirmed_count,
        "needs_followup_count": needs_followup_count,
        "final_note_text_count": final_note_text_count,
        "ready_for_classification": ready_for_classification,
    }
    return {
        **dict(latest),
        "status": merged_metadata["status"],
        "review_id": "merged_saved_note_correction_review",
        "latest_review_id": latest["review_id"],
        "review_mode": "merged_saved_chapter",
        "scope_id": "merged_chapter",
        "review_json": {"note_correction_review": {"items": normalized_items}},
        "normalized_items": normalized_items,
        "review_items": review_items,
        "stats": {
            "item_count": len(review_items),
            "expected_item_count": expected_count,
            "confirmed_count": confirmed_count,
            "needs_followup_count": needs_followup_count,
        },
        "completeness": {
            "expected_count": expected_count,
            "actual_count": len(review_items),
            "missing_note_ids": sorted(expected_keys - set(merged_keys)),
            "duplicate_note_ids": [item["server_note_id"] for item in duplicate_review_items_ignored],
            "unexpected_note_ids": [],
        },
        "merge_scope": {
            "all_valid": ready_for_classification,
            "expected_total": expected_count,
            "validated_items": len(review_items),
            "missing": max(expected_count - len(review_items), 0),
        },
        "merged_metadata": merged_metadata,
        **merged_metadata,
    }


def build_saved_note_correction_review_state(
    *,
    research_db_path: str | Path = DEFAULT_DB_PATH,
    document_id: int,
    chapter_id: int,
    expected_item_count: int = 0,
    expected_sections: list[str] | None = None,
    review_mode: str | None = None,
    scope_id: str | None = None,
) -> dict[str, Any]:
    db_path = Path(research_db_path)
    flags = review_pipeline_safety_flags()
    empty = {
        "status": "not_saved",
        "latest_review_id": None,
        "review_id": None,
        "scope_id": scope_id,
        "review_mode": review_mode,
        "saved_item_count": 0,
        "validated_item_count": 0,
        "human_action_counts": {},
        "confirmed_count": 0,
        "needs_followup_count": 0,
        "final_note_text_count": 0,
        "partial_saved_sections": [],
        "missing_sections": [],
        "pn68_status": "not_saved",
        "ready_for_classification": False,
        "review_count": 0,
        "safety_flags": flags,
        **flags,
    }
    try:
        conn = _connect_ro_existing(db_path)
    except sqlite3.Error:
        return {**empty, "read_error": "review_db_unavailable"}

    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        if not _note_correction_review_schema_ready(conn):
            return empty

        clauses = [
            "document_id = ?",
            "chapter_id = ?",
            "review_status = 'saved'",
        ]
        params: list[Any] = [document_id, chapter_id]
        if review_mode:
            clauses.append("review_mode = ?")
            params.append(review_mode)
        if scope_id:
            clauses.append("COALESCE(scope_id, '') = ?")
            params.append(scope_id)
        review_rows = conn.execute(
            f"""
            SELECT *
            FROM {NOTE_CORRECTION_REVIEW_TABLE}
            WHERE {' AND '.join(clauses)}
            ORDER BY updated_at DESC, created_at DESC
            """,
            params,
        ).fetchall()
        if not review_rows:
            return empty

        review_ids = [str(row["review_id"]) for row in review_rows]
        placeholders = ", ".join("?" for _ in review_ids)
        item_rows = conn.execute(
            f"""
            SELECT items.*, reviews.updated_at AS review_updated_at,
                   reviews.review_mode AS review_mode,
                   reviews.scope_id AS scope_id
            FROM {NOTE_CORRECTION_REVIEW_ITEM_TABLE} AS items
            JOIN {NOTE_CORRECTION_REVIEW_TABLE} AS reviews
              ON reviews.review_id = items.review_id
            WHERE items.review_id IN ({placeholders})
            ORDER BY reviews.updated_at DESC, items.rowid DESC
            """,
            review_ids,
        ).fetchall()
    finally:
        conn.close()

    latest = review_rows[0]
    item_dicts = [dict(row) for row in item_rows]
    latest_items_by_note: dict[str, dict[str, Any]] = {}
    for item in item_dicts:
        note_id = str(item.get("server_note_id") or item.get("client_note_id") or "").strip()
        if note_id and note_id not in latest_items_by_note:
            latest_items_by_note[note_id] = item
    saved_items = list(latest_items_by_note.values())
    saved_item_count = len(saved_items)
    action_counts = dict(Counter(str(item.get("human_action") or "pending") for item in saved_items))
    confirmed_count = sum(1 for item in saved_items if bool(item.get("confirmed_by_user")))
    needs_followup_count = int(action_counts.get("needs_followup") or 0)
    final_note_text_count = sum(1 for item in saved_items if str(item.get("final_note_text") or "").strip())
    section_items_by_note = {
        str(item.get("server_note_id") or item.get("client_note_id") or "").strip(): item
        for item in item_dicts
        if item.get("review_mode") == "section_scoped"
        and str(item.get("server_note_id") or item.get("client_note_id") or "").strip()
    }
    canary_items_shadowed = [
        _ignored_review_item_summary(item, reason="canary_shadowed_by_section_review")
        for item in item_dicts
        if item.get("review_mode") == "canary_subscope"
        and str(item.get("server_note_id") or item.get("client_note_id") or "").strip() in section_items_by_note
    ]
    partial_saved_sections = sorted({
        str(row["scope_id"])
        for row in review_rows
        if row["review_mode"] == "section_scoped" and str(row["scope_id"] or "").strip()
    })
    expected_section_ids = [str(value) for value in (expected_sections or []) if str(value).strip()]
    ready_for_classification = (
        expected_item_count > 0
        and saved_item_count == expected_item_count
        and confirmed_count == expected_item_count
        and needs_followup_count == 0
    )
    status = "saved" if ready_for_classification else "partial"
    missing_sections = (
        [section for section in expected_section_ids if section not in partial_saved_sections]
        if status == "partial"
        else []
    )
    pn68_item = next(
        (item for item in saved_items if item.get("zotero_annotation_key") == "SYNPN068"),
        None,
    )
    pn68_warning = str((pn68_item or {}).get("ai_reviewer_warning") or "").strip()
    source_review_ids = [str(row["review_id"]) for row in review_rows]
    source_section_review_count = sum(1 for row in review_rows if row["review_mode"] == "section_scoped")
    canary_audit_count = sum(1 for row in review_rows if row["review_mode"] == "canary_subscope")
    return {
        "status": status,
        "latest_review_id": latest["review_id"],
        "review_id": latest["review_id"],
        "scope_id": latest["scope_id"],
        "review_mode": latest["review_mode"],
        "saved_item_count": saved_item_count,
        "validated_item_count": saved_item_count,
        "human_action_counts": action_counts,
        "confirmed_count": confirmed_count,
        "needs_followup_count": needs_followup_count,
        "final_note_text_count": final_note_text_count,
        "partial_saved_sections": partial_saved_sections,
        "missing_sections": missing_sections,
        "pn68_status": "saved" if pn68_item else "not_saved",
        "pn68_warning_preserved": bool(pn68_warning),
        "pn68_reviewer_warning": pn68_warning,
        "pn68_correction_status": (pn68_item or {}).get("ai_correction_status"),
        "pn68_issue_type": (pn68_item or {}).get("ai_issue_type"),
        "pn68_evidence_support": (pn68_item or {}).get("ai_evidence_support"),
        "ready_for_classification": ready_for_classification,
        "review_count": len(review_rows),
        "source_review_count": len(review_rows),
        "source_section_review_count": source_section_review_count,
        "canary_audit_count": canary_audit_count,
        "source_review_ids": source_review_ids,
        "source_section_ids": partial_saved_sections,
        "duplicate_review_items_ignored": [],
        "canary_items_shadowed": canary_items_shadowed,
        "classification_package_ready": ready_for_classification,
        "classification_package_status": "ready_for_dry_run_preview" if ready_for_classification else "blocked",
        "schema_version": NOTE_CORRECTION_REVIEW_SAVE_SCHEMA_VERSION,
        "human_audit_schema_version": NOTE_CORRECTION_HUMAN_AUDIT_SCHEMA_VERSION,
        "safety_flags": flags,
        **flags,
    }


def _saved_review_payload(row: sqlite3.Row) -> dict[str, Any]:
    review_json = _loads(row["normalized_review_json"], {})
    normalized_items = (review_json.get("note_correction_review") or {}).get("items") or []
    normalized_by_key = {
        _item_key(item): dict(item)
        for item in normalized_items
        if isinstance(item, Mapping) and _item_key(item)
    }
    return {
        "review_json": review_json,
        "normalized_items": normalized_items,
        "normalized_by_key": normalized_by_key,
        "stats": _loads(row["review_summary_json"], {}),
        "completeness": _loads(row["completeness_json"], {}),
        "merge_scope": _loads(row["merge_scope_json"], None),
    }


def _section_index(section_id: Any) -> int:
    text = str(section_id or "").strip()
    if text in MERGED_NOTE_CORRECTION_SECTION_ORDER:
        return MERGED_NOTE_CORRECTION_SECTION_ORDER.index(text)
    for index, section in enumerate(MERGED_NOTE_CORRECTION_SECTION_ORDER):
        if section and section in text:
            return index
    return len(MERGED_NOTE_CORRECTION_SECTION_ORDER)


def _saved_review_section_sort_key(row: sqlite3.Row) -> tuple[int, int, str, str]:
    review_mode = str(row["review_mode"] or "")
    mode_rank = 0 if review_mode == "section_scoped" else (1 if review_mode != "canary_subscope" else 2)
    return (
        mode_rank,
        _section_index(row["scope_id"]),
        str(row["updated_at"] or ""),
        str(row["review_id"] or ""),
    )


def _saved_classification_review_exists(
    *,
    research_db_path: str | Path,
    document_id: int,
    chapter_id: int,
) -> bool:
    conn = connect_sqlite(Path(research_db_path))
    try:
        conn.row_factory = sqlite3.Row
        if not _note_classification_review_schema_ready(conn):
            return False
        return _latest_saved_classification_review_row(
            conn,
            document_id=document_id,
            chapter_id=chapter_id,
        ) is not None
    finally:
        conn.close()


def load_saved_note_classification_review(
    *,
    research_db_path: str | Path = DEFAULT_DB_PATH,
    document_id: int,
    chapter_id: int,
) -> dict[str, Any] | None:
    db_path = Path(research_db_path)
    conn = _connect_ro_existing(db_path)
    try:
        conn.row_factory = sqlite3.Row
        if not _note_classification_review_schema_ready(conn):
            return None
        row = _latest_saved_classification_review_row(
            conn,
            document_id=document_id,
            chapter_id=chapter_id,
        )
        if not row:
            return None
        item_rows = conn.execute(
            f"""
            SELECT *
            FROM {NOTE_CLASSIFICATION_REVIEW_ITEM_TABLE}
            WHERE review_id = ?
            ORDER BY id
            """.replace("ORDER BY id", "ORDER BY rowid"),
            (row["review_id"],),
        ).fetchall()
        items = [dict(item) for item in item_rows]
        pn68_item = next(
            (
                item
                for item in items
                if item.get("server_note_id") == PRODUCTION_REVIEW_SECTION84_PN68_SERVER_NOTE_ID
                or item.get("zotero_annotation_key") == PRODUCTION_REVIEW_SECTION84_PN68_ZOTERO_KEY
            ),
            None,
        )
        return {
            "status": "saved",
            "review_id": row["review_id"],
            "classification_review_id": row["review_id"],
            "review_type": row["review_type"],
            "review_mode": row["review_mode"],
            "document_id": row["document_id"],
            "chapter_id": row["chapter_id"],
            "source_package_hash": row["source_package_hash"],
            "source_merged_review_hash": row["source_merged_review_hash"],
            "source_item_count": row["source_item_count"],
            "saved_item_count": len(items),
            "validation_status": row["validation_status"],
            "ready_for_object_candidate_generation": bool(row["ready_for_object_candidate_generation"]),
            "object_candidate_generation_status": "requires_explicit_phase7d_gate",
            "label_counts": _loads(row["label_counts_json"], {}),
            "confidence_counts": _loads(row["confidence_counts_json"], {}),
            "pn68_validation": _loads(row["pn68_validation_json"], {}),
            "pn68_classification_label": pn68_item.get("classification_label") if pn68_item else None,
            "pn68_confidence": pn68_item.get("confidence") if pn68_item else None,
            "pn68_warning_preserved": bool(pn68_item),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "llm_called": bool(row["llm_called"]),
            "db_write_performed": bool(row["db_write_performed"]),
            "zotero_write_performed": bool(row["zotero_write_performed"]),
            "vector_write_performed": bool(row["vector_write_performed"]),
            "object_candidates_generated": bool(row["object_candidates_generated"]),
            "relation_generated": bool(row["relation_generated"]),
            "mechanism_generated": bool(row["mechanism_generated"]),
            "items": items,
        }
    finally:
        conn.close()


from app.services.chapter_note_correction_prompt_service import (
    build_chapter_note_correction_package_preview_response,
    build_chapter_note_correction_review_plan,
    build_chapter_note_correction_sections,
)


_CLASSIFICATION_EXPORTS = {
    "build_chapter_note_classification_dry_run_package",
    "build_chapter_note_classification_package",
}


def __getattr__(name: str) -> Any:
    if name in _CLASSIFICATION_EXPORTS:
        from . import classification

        return getattr(classification, name)
    raise AttributeError(name)
