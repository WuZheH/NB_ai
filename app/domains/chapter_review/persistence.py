"""Chapter review persistence responsibilities."""

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

from app.core.database import connect_existing_readwrite_sqlite, connect_readonly_sqlite
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

def _connect_rw_existing(db_path: Path) -> sqlite3.Connection:
    return connect_existing_readwrite_sqlite(db_path)


def _connect_ro_existing(db_path: Path) -> sqlite3.Connection:
    return connect_readonly_sqlite(db_path)


def _chapter_exists(db_path: Path, *, document_id: int, chapter_id: int) -> bool:
    conn = _connect_ro_existing(db_path)
    try:
        if not table_exists(conn, "book_chapters"):
            return False
        row = conn.execute(
            "SELECT 1 FROM book_chapters WHERE id = ? AND document_id = ?",
            (chapter_id, document_id),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def _is_default_research_db_path(db_path: Path) -> bool:
    return db_path.resolve(strict=False) == DEFAULT_DB_PATH.resolve(strict=False)


def _note_correction_review_schema_ready(conn: sqlite3.Connection) -> bool:
    return table_exists(conn, NOTE_CORRECTION_REVIEW_TABLE) and table_exists(conn, NOTE_CORRECTION_REVIEW_ITEM_TABLE)


def _note_classification_review_schema_ready(conn: sqlite3.Connection) -> bool:
    if not table_exists(conn, NOTE_CLASSIFICATION_REVIEW_TABLE) or not table_exists(conn, NOTE_CLASSIFICATION_REVIEW_ITEM_TABLE):
        return False
    review_columns = set(_table_column_names(conn, NOTE_CLASSIFICATION_REVIEW_TABLE))
    item_columns = set(_table_column_names(conn, NOTE_CLASSIFICATION_REVIEW_ITEM_TABLE))
    return {
        "review_id",
        "document_id",
        "chapter_id",
        "review_type",
        "review_mode",
        "source_package_hash",
        "source_item_count",
        "review_json",
        "normalized_items_json",
        "stats_json",
        "label_counts_json",
        "confidence_counts_json",
        "pn68_validation_json",
        "status",
        "validation_status",
        "ready_for_object_candidate_generation",
        "safety_flags_json",
    }.issubset(review_columns) and {
        "review_item_id",
        "review_id",
        "server_note_id",
        "classification_label",
        "confidence",
        "rationale",
        "special_flags_json",
    }.issubset(item_columns)


def _object_candidate_draft_review_schema_ready(conn: sqlite3.Connection) -> bool:
    if not table_exists(conn, OBJECT_CANDIDATE_DRAFT_REVIEW_TABLE) or not table_exists(conn, OBJECT_CANDIDATE_DRAFT_REVIEW_ITEM_TABLE):
        return False
    review_columns = set(_table_column_names(conn, OBJECT_CANDIDATE_DRAFT_REVIEW_TABLE))
    item_columns = set(_table_column_names(conn, OBJECT_CANDIDATE_DRAFT_REVIEW_ITEM_TABLE))
    return {
        "review_id",
        "document_id",
        "chapter_id",
        "source_classification_review_id",
        "dry_run_package_hash",
        "candidate_count",
        "quarantined_count",
        "pn68_quarantined",
        "review_status",
        "confirmation_context",
        "safety_flags_json",
        "approved_objects_created",
    }.issubset(review_columns) and {
        "review_item_id",
        "review_id",
        "candidate_temp_id",
        "object_name",
        "object_type",
        "source_classification_review_id",
        "source_server_note_ids_json",
        "duplicate_group_key",
        "review_status",
        "approved",
        "relation_generated",
        "mechanism_generated",
        "should_save",
    }.issubset(item_columns)


def _object_candidate_human_review_schema_ready(conn: sqlite3.Connection) -> bool:
    if not table_exists(conn, OBJECT_CANDIDATE_HUMAN_REVIEW_TABLE) or not table_exists(conn, OBJECT_CANDIDATE_HUMAN_REVIEW_ITEM_TABLE):
        return False
    review_columns = set(_table_column_names(conn, OBJECT_CANDIDATE_HUMAN_REVIEW_TABLE))
    item_columns = set(_table_column_names(conn, OBJECT_CANDIDATE_HUMAN_REVIEW_ITEM_TABLE))
    return {
        "human_review_id",
        "document_id",
        "chapter_id",
        "source_draft_review_id",
        "source_classification_review_id",
        "candidate_count",
        "approved_count",
        "rejected_count",
        "merged_count",
        "pending_count",
        "pn68_quarantined",
        "review_status",
        "confirmation_context",
        "safety_flags_json",
        "approved_objects_created",
    }.issubset(review_columns) and {
        "review_item_id",
        "human_review_id",
        "source_draft_review_id",
        "source_draft_item_id",
        "candidate_temp_id",
        "action",
        "final_object_name",
        "final_object_type",
        "merge_target_candidate_temp_id",
        "approved_candidate",
        "source_server_note_ids_json",
        "duplicate_group_key",
    }.issubset(item_columns)


def _table_column_names(conn: sqlite3.Connection, table: str) -> list[str]:
    return [str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]


def _saved_review_row(
    conn: sqlite3.Connection,
    *,
    table: str,
    document_id: int,
    chapter_id: int,
    review_type: str,
) -> sqlite3.Row | None:
    return conn.execute(
        f"""
        SELECT *
        FROM {table}
        WHERE document_id = ? AND chapter_id = ? AND review_type = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (document_id, chapter_id, review_type),
    ).fetchone()


def _active_correction_review_row(
    conn: sqlite3.Connection,
    *,
    document_id: int,
    chapter_id: int,
    review_mode: str,
    scope_id: str | None,
    batch_size: int | None,
    batch_index: int | None,
) -> sqlite3.Row | None:
    return conn.execute(
        f"""
        SELECT *
        FROM {NOTE_CORRECTION_REVIEW_TABLE}
        WHERE document_id = ?
          AND chapter_id = ?
          AND review_mode = ?
          AND COALESCE(scope_id, '') = COALESCE(?, '')
          AND COALESCE(batch_size, -1) = COALESCE(?, -1)
          AND COALESCE(batch_index, -1) = COALESCE(?, -1)
          AND review_status IN ('draft', 'saved')
        ORDER BY updated_at DESC
        LIMIT 1
        """,
        (document_id, chapter_id, review_mode, scope_id, batch_size, batch_index),
    ).fetchone()


def _active_correction_review_row_ro(
    db_path: Path,
    *,
    document_id: int,
    chapter_id: int,
    review_mode: str,
    scope_id: str | None,
    batch_size: int | None,
    batch_index: int | None,
) -> dict[str, Any] | None:
    conn = _connect_ro_existing(db_path)
    try:
        conn.row_factory = sqlite3.Row
        if not _note_correction_review_schema_ready(conn):
            return None
        row = _active_correction_review_row(
            conn,
            document_id=document_id,
            chapter_id=chapter_id,
            review_mode=review_mode,
            scope_id=scope_id,
            batch_size=batch_size,
            batch_index=batch_index,
        )
        if not row:
            return None
        return {
            "review_id": row["review_id"],
            "review_status": row["review_status"],
            "review_mode": row["review_mode"],
            "scope_id": row["scope_id"],
            "batch_size": row["batch_size"],
            "batch_index": row["batch_index"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
    finally:
        conn.close()


def _latest_saved_correction_review_row(
    conn: sqlite3.Connection,
    *,
    document_id: int,
    chapter_id: int,
) -> sqlite3.Row | None:
    return conn.execute(
        f"""
        SELECT *
        FROM {NOTE_CORRECTION_REVIEW_TABLE}
        WHERE document_id = ?
          AND chapter_id = ?
          AND review_status = 'saved'
        ORDER BY updated_at DESC
        LIMIT 1
        """,
        (document_id, chapter_id),
    ).fetchone()


def _latest_saved_classification_review_row(
    conn: sqlite3.Connection,
    *,
    document_id: int,
    chapter_id: int,
) -> sqlite3.Row | None:
    return conn.execute(
        f"""
        SELECT *
        FROM {NOTE_CLASSIFICATION_REVIEW_TABLE}
        WHERE document_id = ?
          AND chapter_id = ?
          AND review_type = 'note_classification_review'
          AND status = 'saved'
        ORDER BY updated_at DESC, id DESC
        LIMIT 1
        """,
        (document_id, chapter_id),
    ).fetchone()


def _latest_object_candidate_draft_review_row(
    conn: sqlite3.Connection,
    *,
    document_id: int,
    chapter_id: int,
    source_classification_review_id: str | None = None,
) -> sqlite3.Row | None:
    if source_classification_review_id:
        return conn.execute(
            f"""
            SELECT *
            FROM {OBJECT_CANDIDATE_DRAFT_REVIEW_TABLE}
            WHERE document_id = ?
              AND chapter_id = ?
              AND source_classification_review_id = ?
              AND review_status = 'pending_human_review'
            ORDER BY updated_at DESC, id DESC
            LIMIT 1
            """,
            (document_id, chapter_id, source_classification_review_id),
        ).fetchone()
    return conn.execute(
        f"""
        SELECT *
        FROM {OBJECT_CANDIDATE_DRAFT_REVIEW_TABLE}
        WHERE document_id = ?
          AND chapter_id = ?
          AND review_status = 'pending_human_review'
        ORDER BY updated_at DESC, id DESC
        LIMIT 1
        """,
        (document_id, chapter_id),
    ).fetchone()


def _latest_object_candidate_human_review_row(
    conn: sqlite3.Connection,
    *,
    document_id: int,
    chapter_id: int,
    source_draft_review_id: str | None = None,
) -> sqlite3.Row | None:
    if source_draft_review_id:
        return conn.execute(
            f"""
            SELECT *
            FROM {OBJECT_CANDIDATE_HUMAN_REVIEW_TABLE}
            WHERE document_id = ?
              AND chapter_id = ?
              AND source_draft_review_id = ?
              AND review_status = 'saved'
            ORDER BY updated_at DESC, id DESC
            LIMIT 1
            """,
            (document_id, chapter_id, source_draft_review_id),
        ).fetchone()
    return conn.execute(
        f"""
        SELECT *
        FROM {OBJECT_CANDIDATE_HUMAN_REVIEW_TABLE}
        WHERE document_id = ?
          AND chapter_id = ?
          AND review_status = 'saved'
        ORDER BY updated_at DESC, id DESC
        LIMIT 1
        """,
        (document_id, chapter_id),
    ).fetchone()
