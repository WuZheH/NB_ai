"""Chapter review schema responsibilities."""

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

from .persistence import (
    _connect_ro_existing,
    _connect_rw_existing,
    _note_classification_review_schema_ready,
    _object_candidate_draft_review_schema_ready,
    _object_candidate_human_review_schema_ready,
    _table_column_names,
)

from .safety import (
    review_pipeline_safety_flags,
)

def ensure_chapter_review_tables(conn: sqlite3.Connection) -> None:
    """Create review tables for temporary fixtures.

    Production migrations are intentionally not run by the API. Tests may call
    this helper against copied or in-memory databases.
    """
    for statement in note_correction_review_schema_sql():
        conn.execute(statement)
    for statement in note_classification_review_schema_sql():
        conn.execute(statement)
    for statement in object_candidate_draft_review_schema_sql():
        conn.execute(statement)
    for statement in object_candidate_human_review_schema_sql():
        conn.execute(statement)


def note_classification_review_schema_sql() -> list[str]:
    return [
        f"""
        CREATE TABLE IF NOT EXISTS {NOTE_CLASSIFICATION_REVIEW_TABLE} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            review_id TEXT NOT NULL UNIQUE,
            document_id INTEGER NOT NULL,
            chapter_id INTEGER NOT NULL,
            review_type TEXT NOT NULL,
            review_mode TEXT NOT NULL CHECK(review_mode IN ('manual_json')),
            source_package_hash TEXT NOT NULL,
            source_merged_review_hash TEXT NULL,
            source_item_count INTEGER NOT NULL,
            review_hash TEXT NOT NULL,
            review_json TEXT NOT NULL,
            normalized_items_json TEXT NOT NULL,
            stats_json TEXT NOT NULL,
            label_counts_json TEXT NOT NULL,
            confidence_counts_json TEXT NOT NULL,
            pn68_validation_json TEXT NOT NULL,
            confirmation_context TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('saved', 'superseded')),
            validation_status TEXT NOT NULL CHECK(validation_status IN ('valid')),
            ready_for_object_candidate_generation INTEGER NOT NULL DEFAULT 0,
            llm_called INTEGER NOT NULL DEFAULT 0,
            db_write_performed INTEGER NOT NULL DEFAULT 1,
            zotero_write_performed INTEGER NOT NULL DEFAULT 0,
            vector_write_performed INTEGER NOT NULL DEFAULT 0,
            object_candidates_generated INTEGER NOT NULL DEFAULT 0,
            relation_generated INTEGER NOT NULL DEFAULT 0,
            mechanism_generated INTEGER NOT NULL DEFAULT 0,
            safety_flags_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        f"""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_note_classification_reviews_active_chapter
        ON {NOTE_CLASSIFICATION_REVIEW_TABLE} (document_id, chapter_id, review_type)
        WHERE status = 'saved'
        """,
        f"""
        CREATE INDEX IF NOT EXISTS ix_note_classification_reviews_chapter
        ON {NOTE_CLASSIFICATION_REVIEW_TABLE} (document_id, chapter_id, status)
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {NOTE_CLASSIFICATION_REVIEW_ITEM_TABLE} (
            review_item_id TEXT PRIMARY KEY,
            review_id TEXT NOT NULL,
            server_note_id TEXT NOT NULL,
            client_note_id TEXT NULL,
            zotero_annotation_key TEXT NULL,
            section_id TEXT NULL,
            page_label TEXT NULL,
            pdf_page INTEGER NULL,
            original_note_text TEXT NOT NULL,
            selected_text TEXT NULL,
            classification_label TEXT NOT NULL,
            confidence TEXT NOT NULL,
            rationale TEXT NOT NULL,
            evidence_alignment_status TEXT NULL,
            special_flags_json TEXT NOT NULL,
            source_correction_item_id TEXT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(review_id) REFERENCES {NOTE_CLASSIFICATION_REVIEW_TABLE}(review_id)
        )
        """,
        f"""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_note_classification_review_items_review_note
        ON {NOTE_CLASSIFICATION_REVIEW_ITEM_TABLE} (review_id, server_note_id)
        """,
        f"""
        CREATE INDEX IF NOT EXISTS ix_note_classification_review_items_server_note
        ON {NOTE_CLASSIFICATION_REVIEW_ITEM_TABLE} (server_note_id)
        """,
    ]


def object_candidate_draft_review_schema_sql() -> list[str]:
    return [
        f"""
        CREATE TABLE IF NOT EXISTS {OBJECT_CANDIDATE_DRAFT_REVIEW_TABLE} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            review_id TEXT NOT NULL UNIQUE,
            document_id INTEGER NOT NULL,
            chapter_id INTEGER NOT NULL,
            review_mode TEXT NOT NULL CHECK(review_mode IN ('dry_run_draft_save')),
            source_classification_review_id TEXT NOT NULL,
            source_package_hash TEXT NULL,
            dry_run_package_hash TEXT NOT NULL,
            candidate_count INTEGER NOT NULL,
            quarantined_count INTEGER NOT NULL,
            pn68_quarantined INTEGER NOT NULL DEFAULT 0,
            review_status TEXT NOT NULL CHECK(review_status IN ('pending_human_review', 'superseded')),
            confirmation_context TEXT NOT NULL,
            safety_flags_json TEXT NOT NULL,
            llm_called INTEGER NOT NULL DEFAULT 0,
            db_write_performed INTEGER NOT NULL DEFAULT 1,
            zotero_write_performed INTEGER NOT NULL DEFAULT 0,
            vector_write_performed INTEGER NOT NULL DEFAULT 0,
            object_candidates_generated INTEGER NOT NULL DEFAULT 0,
            relation_generated INTEGER NOT NULL DEFAULT 0,
            mechanism_generated INTEGER NOT NULL DEFAULT 0,
            approved_objects_created INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        f"""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_object_candidate_draft_reviews_active_chapter
        ON {OBJECT_CANDIDATE_DRAFT_REVIEW_TABLE} (document_id, chapter_id, source_classification_review_id)
        WHERE review_status = 'pending_human_review'
        """,
        f"""
        CREATE INDEX IF NOT EXISTS ix_object_candidate_draft_reviews_chapter
        ON {OBJECT_CANDIDATE_DRAFT_REVIEW_TABLE} (document_id, chapter_id, review_status)
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {OBJECT_CANDIDATE_DRAFT_REVIEW_ITEM_TABLE} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            review_item_id TEXT NOT NULL UNIQUE,
            review_id TEXT NOT NULL,
            candidate_temp_id TEXT NOT NULL,
            document_id INTEGER NOT NULL,
            chapter_id INTEGER NOT NULL,
            object_name TEXT NOT NULL,
            object_type TEXT NOT NULL,
            source_classification_review_id TEXT NOT NULL,
            source_server_note_ids_json TEXT NOT NULL,
            source_labels_json TEXT NOT NULL DEFAULT '[]',
            evidence_chunk_ids_json TEXT NOT NULL DEFAULT '[]',
            page_labels_json TEXT NOT NULL DEFAULT '[]',
            confidence REAL NULL,
            rationale TEXT NULL,
            duplicate_group_key TEXT NOT NULL,
            review_status TEXT NOT NULL CHECK(review_status IN ('pending_human_review', 'accepted', 'rejected', 'merged')),
            approved INTEGER NOT NULL DEFAULT 0,
            relation_generated INTEGER NOT NULL DEFAULT 0,
            mechanism_generated INTEGER NOT NULL DEFAULT 0,
            should_save INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            FOREIGN KEY(review_id) REFERENCES {OBJECT_CANDIDATE_DRAFT_REVIEW_TABLE}(review_id)
        )
        """,
        f"""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_object_candidate_draft_items_review_candidate
        ON {OBJECT_CANDIDATE_DRAFT_REVIEW_ITEM_TABLE} (review_id, candidate_temp_id)
        """,
        f"""
        CREATE INDEX IF NOT EXISTS ix_object_candidate_draft_items_review_status
        ON {OBJECT_CANDIDATE_DRAFT_REVIEW_ITEM_TABLE} (document_id, chapter_id, review_status)
        """,
        f"""
        CREATE INDEX IF NOT EXISTS ix_object_candidate_draft_items_duplicate_group
        ON {OBJECT_CANDIDATE_DRAFT_REVIEW_ITEM_TABLE} (duplicate_group_key)
        """,
    ]


def object_candidate_human_review_schema_sql() -> list[str]:
    return [
        f"""
        CREATE TABLE IF NOT EXISTS {OBJECT_CANDIDATE_HUMAN_REVIEW_TABLE} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            human_review_id TEXT NOT NULL UNIQUE,
            document_id INTEGER NOT NULL,
            chapter_id INTEGER NOT NULL,
            source_draft_review_id TEXT NOT NULL,
            source_classification_review_id TEXT NOT NULL,
            review_mode TEXT NOT NULL CHECK(review_mode IN ('manual_object_candidate_review')),
            review_payload_hash TEXT NOT NULL,
            candidate_count INTEGER NOT NULL,
            approved_count INTEGER NOT NULL,
            rejected_count INTEGER NOT NULL,
            edited_count INTEGER NOT NULL,
            merged_count INTEGER NOT NULL,
            pending_count INTEGER NOT NULL,
            pn68_quarantined INTEGER NOT NULL DEFAULT 0,
            review_status TEXT NOT NULL CHECK(review_status IN ('saved', 'superseded')),
            confirmation_context TEXT NOT NULL,
            safety_flags_json TEXT NOT NULL,
            llm_called INTEGER NOT NULL DEFAULT 0,
            db_write_performed INTEGER NOT NULL DEFAULT 1,
            zotero_write_performed INTEGER NOT NULL DEFAULT 0,
            vector_write_performed INTEGER NOT NULL DEFAULT 0,
            object_candidates_generated INTEGER NOT NULL DEFAULT 0,
            approved_objects_created INTEGER NOT NULL DEFAULT 0,
            relation_generated INTEGER NOT NULL DEFAULT 0,
            mechanism_generated INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(source_draft_review_id) REFERENCES {OBJECT_CANDIDATE_DRAFT_REVIEW_TABLE}(review_id)
        )
        """,
        f"""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_object_candidate_human_reviews_active_draft
        ON {OBJECT_CANDIDATE_HUMAN_REVIEW_TABLE} (source_draft_review_id)
        WHERE review_status = 'saved'
        """,
        f"""
        CREATE INDEX IF NOT EXISTS ix_object_candidate_human_reviews_chapter
        ON {OBJECT_CANDIDATE_HUMAN_REVIEW_TABLE} (document_id, chapter_id, review_status)
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {OBJECT_CANDIDATE_HUMAN_REVIEW_ITEM_TABLE} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            review_item_id TEXT NOT NULL UNIQUE,
            human_review_id TEXT NOT NULL,
            source_draft_review_id TEXT NOT NULL,
            source_draft_item_id TEXT NOT NULL,
            candidate_temp_id TEXT NOT NULL,
            document_id INTEGER NOT NULL,
            chapter_id INTEGER NOT NULL,
            action TEXT NOT NULL CHECK(action IN ('approve', 'reject', 'edit', 'merge', 'pending')),
            final_object_name TEXT NULL,
            final_object_type TEXT NULL,
            merge_target_candidate_temp_id TEXT NULL,
            merge_group_key TEXT NULL,
            human_note TEXT NULL,
            approved_candidate INTEGER NOT NULL DEFAULT 0,
            source_server_note_ids_json TEXT NOT NULL,
            source_labels_json TEXT NOT NULL DEFAULT '[]',
            evidence_chunk_ids_json TEXT NOT NULL DEFAULT '[]',
            page_labels_json TEXT NOT NULL DEFAULT '[]',
            duplicate_group_key TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(human_review_id) REFERENCES {OBJECT_CANDIDATE_HUMAN_REVIEW_TABLE}(human_review_id)
        )
        """,
        f"""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_object_candidate_human_items_review_candidate
        ON {OBJECT_CANDIDATE_HUMAN_REVIEW_ITEM_TABLE} (human_review_id, candidate_temp_id)
        """,
        f"""
        CREATE INDEX IF NOT EXISTS ix_object_candidate_human_items_action
        ON {OBJECT_CANDIDATE_HUMAN_REVIEW_ITEM_TABLE} (document_id, chapter_id, action)
        """,
    ]


def note_correction_review_schema_sql() -> list[str]:
    return [
        f"""
        CREATE TABLE IF NOT EXISTS {NOTE_CORRECTION_REVIEW_TABLE} (
            review_id TEXT PRIMARY KEY,
            document_id INTEGER NOT NULL,
            chapter_id INTEGER NOT NULL,
            review_mode TEXT NOT NULL CHECK(review_mode IN ('full_chapter', 'section_scoped', 'fixed_size_batch', 'canary_subscope')),
            scope_id TEXT NULL,
            batch_size INTEGER NULL,
            batch_index INTEGER NULL,
            source_package_hash TEXT NULL,
            normalized_review_json TEXT NOT NULL,
            review_summary_json TEXT NOT NULL,
            completeness_json TEXT NOT NULL,
            merge_scope_json TEXT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            created_by TEXT NOT NULL DEFAULT 'user',
            review_status TEXT NOT NULL CHECK(review_status IN ('draft', 'saved', 'superseded')),
            confirmation_context TEXT NOT NULL,
            safety_flags_json TEXT NOT NULL
        )
        """,
        f"""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_note_correction_reviews_active_scope
        ON {NOTE_CORRECTION_REVIEW_TABLE} (
            document_id,
            chapter_id,
            review_mode,
            COALESCE(scope_id, ''),
            COALESCE(batch_size, -1),
            COALESCE(batch_index, -1)
        )
        WHERE review_status IN ('draft', 'saved')
        """,
        f"""
        CREATE INDEX IF NOT EXISTS ix_note_correction_reviews_chapter
        ON {NOTE_CORRECTION_REVIEW_TABLE} (document_id, chapter_id, review_status)
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {NOTE_CORRECTION_REVIEW_ITEM_TABLE} (
            review_item_id TEXT PRIMARY KEY,
            review_id TEXT NOT NULL,
            server_note_id TEXT NOT NULL,
            client_note_id TEXT NULL,
            zotero_annotation_key TEXT NULL,
            page INTEGER NULL,
            original_note_text TEXT NOT NULL,
            selected_text TEXT NULL,
            ai_correction_status TEXT NOT NULL,
            ai_issue_type TEXT NOT NULL,
            ai_explanation TEXT NOT NULL,
            ai_suggested_revision TEXT NULL,
            ai_evidence_support TEXT NOT NULL,
            ai_confidence REAL NULL,
            ai_reviewer_warning TEXT NULL,
            human_action TEXT NOT NULL CHECK(human_action IN ('pending', 'keep_original', 'ai_revision_accepted', 'manually_edited', 'needs_followup')),
            final_note_text TEXT NULL,
            confirmed_by_user INTEGER NOT NULL DEFAULT 0,
            writeback_intent TEXT NOT NULL DEFAULT 'none' CHECK(writeback_intent IN ('none', 'planned')),
            writeback_status TEXT NOT NULL DEFAULT 'not_started' CHECK(writeback_status IN ('not_started', 'queued', 'written', 'failed')),
            writeback_target TEXT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(review_id) REFERENCES {NOTE_CORRECTION_REVIEW_TABLE}(review_id)
        )
        """,
        f"""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_note_correction_review_items_review_note
        ON {NOTE_CORRECTION_REVIEW_ITEM_TABLE} (review_id, server_note_id)
        """,
        f"""
        CREATE INDEX IF NOT EXISTS ix_note_correction_review_items_server_note
        ON {NOTE_CORRECTION_REVIEW_ITEM_TABLE} (server_note_id)
        """,
    ]


def build_note_classification_review_schema_audit(
    *,
    research_db_path: str | Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    db_path = Path(research_db_path)
    conn = _connect_ro_existing(db_path)
    try:
        conn.row_factory = sqlite3.Row
        tables: dict[str, Any] = {}
        for table in PRODUCTION_NOTE_CLASSIFICATION_WRITE_TABLES:
            exists = table_exists(conn, table)
            tables[table] = {
                "exists": exists,
                "columns": _table_column_names(conn, table) if exists else [],
                "count": int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]) if exists else None,
            }
        ready = _note_classification_review_schema_ready(conn)
        integrity = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
        foreign_key_check = [tuple(row) for row in conn.execute("PRAGMA foreign_key_check").fetchall()]
    finally:
        conn.close()
    flags = review_pipeline_safety_flags()
    return {
        "status": "ready" if ready else "missing",
        "mode": "r3_phase7c_note_classification_review_schema_audit",
        "db_path": str(db_path),
        "schema_ready": ready,
        "schema_version": NOTE_CLASSIFICATION_REVIEW_SAVE_SCHEMA_VERSION,
        "tables": tables,
        "integrity_check": integrity,
        "foreign_key_check": foreign_key_check,
        "db_hash_sha256": hashlib.sha256(db_path.read_bytes()).hexdigest(),
        "db_write_performed": False,
        "safety_flags": flags,
        **flags,
    }


def ensure_note_classification_review_tables(
    *,
    research_db_path: str | Path = DEFAULT_DB_PATH,
    execute: bool = False,
) -> dict[str, Any]:
    db_path = Path(research_db_path)
    before = build_note_classification_review_schema_audit(research_db_path=db_path)
    statements = note_classification_review_schema_sql()
    executed_statement_count = 0
    if execute:
        conn = _connect_rw_existing(db_path)
        try:
            for statement in statements:
                conn.execute(statement)
                executed_statement_count += 1
            conn.commit()
        finally:
            conn.close()
    after = build_note_classification_review_schema_audit(research_db_path=db_path)
    return {
        "status": "migrated" if execute else "dry_run",
        "mode": "r3_phase7c_note_classification_review_schema_migration",
        "execute": bool(execute),
        "db_path": str(db_path),
        "schema_version": NOTE_CLASSIFICATION_REVIEW_SAVE_SCHEMA_VERSION,
        "planned_statement_count": len(statements),
        "executed_statement_count": executed_statement_count,
        "before": before,
        "after": after,
        "schema_ready": bool(after.get("schema_ready")),
        "db_write_performed": bool(execute and before.get("db_hash_sha256") != after.get("db_hash_sha256")),
        "zotero_db_write_performed": False,
        "vector_store_write_performed": False,
        "llm_called": False,
        "object_candidates_generated": False,
        "relation_generated": False,
        "mechanism_generated": False,
    }


def build_object_candidate_draft_review_schema_audit(
    *,
    research_db_path: str | Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    db_path = Path(research_db_path)
    conn = _connect_ro_existing(db_path)
    try:
        conn.row_factory = sqlite3.Row
        tables: dict[str, Any] = {}
        for table in PRODUCTION_OBJECT_CANDIDATE_DRAFT_WRITE_TABLES:
            exists = table_exists(conn, table)
            tables[table] = {
                "exists": exists,
                "columns": _table_column_names(conn, table) if exists else [],
                "count": int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]) if exists else 0,
            }
        object_candidates_exists = table_exists(conn, "object_candidates")
        object_candidates_columns = _table_column_names(conn, "object_candidates") if object_candidates_exists else []
        object_candidate_statuses: dict[str, Any] = {}
        if object_candidates_exists:
            if "review_status" in object_candidates_columns:
                object_candidate_statuses["review_status"] = dict(
                    conn.execute(
                        "SELECT COALESCE(review_status, '<null>'), COUNT(*) FROM object_candidates GROUP BY COALESCE(review_status, '<null>')"
                    ).fetchall()
                )
            if "status" in object_candidates_columns:
                object_candidate_statuses["status"] = dict(
                    conn.execute(
                        "SELECT COALESCE(status, '<null>'), COUNT(*) FROM object_candidates GROUP BY COALESCE(status, '<null>')"
                    ).fetchall()
                )
            if {"document_id", "chapter_id"}.issubset(set(object_candidates_columns)):
                object_candidate_statuses["ch8_count"] = int(
                    conn.execute(
                        "SELECT COUNT(*) FROM object_candidates WHERE document_id = ? AND chapter_id = ?",
                        (PRODUCTION_OBJECT_CANDIDATE_DRAFT_DOCUMENT_ID, PRODUCTION_OBJECT_CANDIDATE_DRAFT_CHAPTER_ID),
                    ).fetchone()[0]
                )
        object_candidate_source_linkage = {
            "source_note_ids_json": "source_note_ids_json" in object_candidates_columns,
            "note_refs_json": "note_refs_json" in object_candidates_columns,
            "source_classification_review_id": "source_classification_review_id" in object_candidates_columns,
        }
        ready = _object_candidate_draft_review_schema_ready(conn)
        integrity = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
        foreign_key_check = [tuple(row) for row in conn.execute("PRAGMA foreign_key_check").fetchall()]
    finally:
        conn.close()
    flags = review_pipeline_safety_flags()
    return {
        "status": "ready" if ready else "missing",
        "mode": "r3_phase7e_object_candidate_draft_schema_audit",
        "db_path": str(db_path),
        "schema_ready": ready,
        "schema_version": OBJECT_CANDIDATE_DRAFT_SAVE_SCHEMA_VERSION,
        "tables": tables,
        "existing_object_candidates": {
            "exists": object_candidates_exists,
            "columns": object_candidates_columns,
            "status_distribution": object_candidate_statuses,
            "source_linkage": object_candidate_source_linkage,
            "reuse_for_phase7e": False,
            "reuse_reason": "existing object_candidates only allows accepted/edited review_status and would pollute approved object search.",
        },
        "integrity_check": integrity,
        "foreign_key_check": foreign_key_check,
        "db_hash_sha256": hashlib.sha256(db_path.read_bytes()).hexdigest(),
        "db_write_performed": False,
        "safety_flags": flags,
        **flags,
    }


def ensure_object_candidate_draft_review_tables(
    *,
    research_db_path: str | Path = DEFAULT_DB_PATH,
    execute: bool = False,
) -> dict[str, Any]:
    db_path = Path(research_db_path)
    before = build_object_candidate_draft_review_schema_audit(research_db_path=db_path)
    statements = object_candidate_draft_review_schema_sql()
    executed_statement_count = 0
    if execute:
        conn = _connect_rw_existing(db_path)
        try:
            for statement in statements:
                conn.execute(statement)
                executed_statement_count += 1
            conn.commit()
        finally:
            conn.close()
    after = build_object_candidate_draft_review_schema_audit(research_db_path=db_path)
    return {
        "status": "migrated" if execute else "dry_run",
        "mode": "r3_phase7e_object_candidate_draft_schema_migration",
        "execute": bool(execute),
        "db_path": str(db_path),
        "schema_version": OBJECT_CANDIDATE_DRAFT_SAVE_SCHEMA_VERSION,
        "planned_statement_count": len(statements),
        "executed_statement_count": executed_statement_count,
        "before": before,
        "after": after,
        "schema_ready": bool(after.get("schema_ready")),
        "db_write_performed": bool(execute and before.get("db_hash_sha256") != after.get("db_hash_sha256")),
        "zotero_db_write_performed": False,
        "vector_store_write_performed": False,
        "llm_called": False,
        "object_candidates_generated": False,
        "relation_generated": False,
        "mechanism_generated": False,
    }


def build_object_candidate_human_review_schema_audit(
    *,
    research_db_path: str | Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    db_path = Path(research_db_path)
    conn = _connect_ro_existing(db_path)
    try:
        conn.row_factory = sqlite3.Row
        tables: dict[str, Any] = {}
        for table in PRODUCTION_OBJECT_CANDIDATE_HUMAN_REVIEW_WRITE_TABLES:
            exists = table_exists(conn, table)
            tables[table] = {
                "exists": exists,
                "columns": _table_column_names(conn, table) if exists else [],
                "count": int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]) if exists else 0,
            }
        ready = _object_candidate_human_review_schema_ready(conn)
        draft_ready = _object_candidate_draft_review_schema_ready(conn)
        object_candidates_count = (
            int(conn.execute("SELECT COUNT(*) FROM object_candidates").fetchone()[0])
            if table_exists(conn, "object_candidates")
            else 0
        )
        integrity = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
        foreign_key_check = [tuple(row) for row in conn.execute("PRAGMA foreign_key_check").fetchall()]
    finally:
        conn.close()
    flags = review_pipeline_safety_flags()
    return {
        "status": "ready" if ready else "missing",
        "mode": "r3_phase7f_object_candidate_human_review_schema_audit",
        "db_path": str(db_path),
        "schema_ready": ready,
        "draft_schema_ready": draft_ready,
        "schema_version": OBJECT_CANDIDATE_HUMAN_REVIEW_SCHEMA_VERSION,
        "tables": tables,
        "schema_decision": "new human review result tables; original draft rows and object_candidates remain unchanged",
        "object_candidates_count": object_candidates_count,
        "integrity_check": integrity,
        "foreign_key_check": foreign_key_check,
        "db_hash_sha256": hashlib.sha256(db_path.read_bytes()).hexdigest(),
        "db_write_performed": False,
        "safety_flags": flags,
        **flags,
    }


def ensure_object_candidate_human_review_tables(
    *,
    research_db_path: str | Path = DEFAULT_DB_PATH,
    execute: bool = False,
) -> dict[str, Any]:
    db_path = Path(research_db_path)
    before = build_object_candidate_human_review_schema_audit(research_db_path=db_path)
    statements = object_candidate_human_review_schema_sql()
    executed_statement_count = 0
    if execute:
        conn = _connect_rw_existing(db_path)
        try:
            for statement in statements:
                conn.execute(statement)
                executed_statement_count += 1
            conn.commit()
        finally:
            conn.close()
    after = build_object_candidate_human_review_schema_audit(research_db_path=db_path)
    return {
        "status": "migrated" if execute else "dry_run",
        "mode": "r3_phase7f_object_candidate_human_review_schema_migration",
        "execute": bool(execute),
        "db_path": str(db_path),
        "schema_version": OBJECT_CANDIDATE_HUMAN_REVIEW_SCHEMA_VERSION,
        "planned_statement_count": len(statements),
        "executed_statement_count": executed_statement_count,
        "before": before,
        "after": after,
        "schema_ready": bool(after.get("schema_ready")),
        "db_write_performed": bool(execute and before.get("db_hash_sha256") != after.get("db_hash_sha256")),
        "zotero_db_write_performed": False,
        "vector_store_write_performed": False,
        "llm_called": False,
        "object_candidates_generated": False,
        "approved_objects_created": False,
        "relation_generated": False,
        "mechanism_generated": False,
    }
