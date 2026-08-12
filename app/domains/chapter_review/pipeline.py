"""Chapter review pipeline responsibilities."""

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

from .gates import (
    build_note_correction_review_production_canary_preflight,
    build_note_correction_review_save_readiness,
    build_note_correction_review_save_request_gate,
)

from .loading import (
    build_note_correction_production_db_snapshot,
)

from .normalization import (
    _canary_subscope_response_metadata,
    _merge_preview_summary,
    _merge_scope_complete,
    _utc_now,
)

from .persistence import (
    _active_correction_review_row,
    _active_correction_review_row_ro,
    _connect_rw_existing,
    _note_correction_review_schema_ready,
)

from .safety import (
    review_pipeline_safety_flags,
)

from .validation import (
    _normalize_human_audit_items,
    _normalized_review_root,
)

def build_note_correction_review_save_canary_plan(
    *,
    research_db_path: str | Path = DEFAULT_DB_PATH,
    document_id: int,
    chapter_id: int,
    review_payload: str | Mapping[str, Any],
    confirmation_context: str | None,
    review_mode: str = "full_chapter",
    scope_id: str | None = None,
    batch_size: int | None = None,
    batch_index: int | None = None,
    parent_review_mode: str | None = None,
    parent_scope_id: str | None = None,
    selected_server_note_ids: list[str] | None = None,
    selected_note_ids: list[str] | None = None,
    human_audit_items: list[Mapping[str, Any]] | None = None,
    merge_preview: Mapping[str, Any] | None = None,
    source_package_hash: str | None = None,
    supersede_existing: bool = False,
) -> dict[str, Any]:
    db_path = Path(research_db_path)
    preflight = build_note_correction_review_production_canary_preflight(
        research_db_path=db_path,
        document_id=document_id,
        chapter_id=chapter_id,
    )
    before_snapshot = build_note_correction_production_db_snapshot(research_db_path=db_path)
    package = _expected_correction_package(
        research_db_path=db_path,
        document_id=document_id,
        chapter_id=chapter_id,
        review_mode=review_mode,
        scope_id=scope_id,
        batch_size=batch_size,
        batch_index=batch_index,
        parent_review_mode=parent_review_mode,
        parent_scope_id=parent_scope_id,
        selected_server_note_ids=selected_server_note_ids,
        selected_note_ids=selected_note_ids,
    )
    if review_mode == "canary_subscope":
        scope_id = scope_id or str(package.get("scope_id") or "")
        source_package_hash = source_package_hash or str(package.get("source_package_hash") or "")
    validation = validate_chapter_note_correction_review(
        research_db_path=db_path,
        document_id=document_id,
        chapter_id=chapter_id,
        review_payload=review_payload,
        expected_package=package,
        require_pn68=bool((package.get("scope") or {}).get("pn68_in_scope")),
    )
    if not validation.get("valid"):
        return _blocked_canary_plan_response(
            document_id=document_id,
            chapter_id=chapter_id,
            reason="validator_failed",
            preflight=preflight,
            before_snapshot=before_snapshot,
            validation=validation,
        )

    audit_result = _normalize_human_audit_items(
        validation=validation,
        package=package,
        human_audit_items=human_audit_items or [],
    )
    if audit_result["errors"]:
        return _blocked_canary_plan_response(
            document_id=document_id,
            chapter_id=chapter_id,
            reason="human_audit_invalid",
            preflight=preflight,
            before_snapshot=before_snapshot,
            validation=validation,
            human_audit_errors=audit_result["errors"],
        )
    if audit_result["needs_followup_count"]:
        return _blocked_canary_plan_response(
            document_id=document_id,
            chapter_id=chapter_id,
            reason="needs_followup_items",
            preflight=preflight,
            before_snapshot=before_snapshot,
            validation=validation,
            human_audit_errors=["needs_followup_items must be resolved before saving note_correction_review"],
        )

    existing = _active_correction_review_row_ro(
        db_path,
        document_id=document_id,
        chapter_id=chapter_id,
        review_mode=review_mode,
        scope_id=scope_id,
        batch_size=batch_size,
        batch_index=batch_index,
    )
    action_counts = dict(Counter(item["human_action"] for item in audit_result["items"]))
    audit_trace_summary = _build_note_correction_save_audit_trace(
        document_id=document_id,
        chapter_id=chapter_id,
        review_mode=review_mode,
        scope_id=scope_id,
        batch_size=batch_size,
        batch_index=batch_index,
        confirmation_context=confirmation_context,
        action_counts=action_counts,
        audit_result=audit_result,
        supersede_existing=supersede_existing,
        existing_review_id=existing.get("review_id") if existing else None,
        event="note_correction_review_save_canary_plan",
        no_write=True,
    )
    duplicate_blocks_insert = bool(existing and not supersede_existing)
    planned_review_write_count = 0 if duplicate_blocks_insert else 1
    planned_item_write_count = 0 if duplicate_blocks_insert else len(audit_result["items"])
    after_snapshot = build_note_correction_production_db_snapshot(research_db_path=db_path)
    flags = review_pipeline_safety_flags()
    return {
        "status": "planned",
        "mode": "note_correction_review_save_canary_plan",
        "document_id": document_id,
        "chapter_id": chapter_id,
        "review_mode": review_mode,
        "scope_id": scope_id,
        "batch_size": batch_size,
        "batch_index": batch_index,
        "schema_version": NOTE_CORRECTION_REVIEW_SAVE_SCHEMA_VERSION,
        "human_audit_schema_version": NOTE_CORRECTION_HUMAN_AUDIT_SCHEMA_VERSION,
        "expected_item_count": int((validation.get("completeness") or {}).get("expected_count") or len(package.get("correction_candidates") or [])),
        "actual_item_count": int((validation.get("completeness") or {}).get("actual_count") or len(validation.get("normalized_preview") or [])),
        "final_note_text_count": int(audit_result.get("final_note_text_count") or 0),
        "confirmed_item_count": int(audit_result.get("confirmed_count") or 0),
        "human_action_counts": action_counts,
        "audit_trace_summary": audit_trace_summary,
        "supersede_target": existing if supersede_existing else None,
        "duplicate_target": existing if duplicate_blocks_insert else None,
        "source_package_hash": source_package_hash,
        **_canary_subscope_response_metadata(package, validation),
        "merge_preview_summary": _merge_preview_summary(merge_preview),
        "planned_review_write_count": planned_review_write_count,
        "planned_item_write_count": planned_item_write_count,
        "execution_allowed": bool(preflight.get("production_review_write_allowed")) and not duplicate_blocks_insert,
        "current_blockers": list(preflight.get("current_blockers") or []),
        "production_canary_preflight": preflight,
        "db_snapshot_before": before_snapshot,
        "db_snapshot_after": after_snapshot,
        "db_snapshot_unchanged": before_snapshot["db_hash_sha256"] == after_snapshot["db_hash_sha256"]
        and before_snapshot["counts"] == after_snapshot["counts"],
        "validation": validation,
        "no_write": True,
        "canary_plan_only": True,
        "real_save_api_called": False,
        "safety_flags": flags,
        **flags,
    }


def save_chapter_note_correction_review(
    *,
    research_db_path: str | Path = DEFAULT_DB_PATH,
    document_id: int,
    chapter_id: int,
    review_payload: str | Mapping[str, Any],
    confirm_write: bool,
    confirmation_context: str | None,
    review_mode: str = "full_chapter",
    scope_id: str | None = None,
    batch_size: int | None = None,
    batch_index: int | None = None,
    parent_review_mode: str | None = None,
    parent_scope_id: str | None = None,
    selected_server_note_ids: list[str] | None = None,
    selected_note_ids: list[str] | None = None,
    human_audit_items: list[Mapping[str, Any]] | None = None,
    merge_preview: Mapping[str, Any] | None = None,
    source_package_hash: str | None = None,
    supersede_existing: bool = False,
    canary_subscope: bool = False,
) -> dict[str, Any]:
    package = _expected_correction_package(
        research_db_path=research_db_path,
        document_id=document_id,
        chapter_id=chapter_id,
        review_mode=review_mode,
        scope_id=scope_id,
        batch_size=batch_size,
        batch_index=batch_index,
        parent_review_mode=parent_review_mode,
        parent_scope_id=parent_scope_id,
        selected_server_note_ids=selected_server_note_ids,
        selected_note_ids=selected_note_ids,
    )
    if review_mode == "canary_subscope":
        scope_id = scope_id or str(package.get("scope_id") or "")
        source_package_hash = source_package_hash or str(package.get("source_package_hash") or "")
    validation = validate_chapter_note_correction_review(
        research_db_path=research_db_path,
        document_id=document_id,
        chapter_id=chapter_id,
        review_payload=review_payload,
        expected_package=package,
        require_pn68=bool((package.get("scope") or {}).get("pn68_in_scope")),
    )
    if not validation.get("valid"):
        return _blocked_save_response(
            document_id,
            chapter_id,
            "validator_failed",
            validation=validation,
        )
    if not confirm_write:
        return _blocked_save_response(
            document_id,
            chapter_id,
            "confirm_write_required",
            validation=validation,
        )
    if confirmation_context != NOTE_CORRECTION_SAVE_CONTEXT:
        return _blocked_save_response(
            document_id,
            chapter_id,
            "confirmation_context_invalid",
            validation=validation,
        )

    audit_result = _normalize_human_audit_items(
        validation=validation,
        package=package,
        human_audit_items=human_audit_items or [],
    )
    if audit_result["errors"]:
        return _blocked_save_response(
            document_id,
            chapter_id,
            "human_audit_invalid",
            validation=validation,
            human_audit_errors=audit_result["errors"],
            review_storage_schema="not_checked",
        )
    if audit_result["needs_followup_count"]:
        return _blocked_save_response(
            document_id,
            chapter_id,
            "needs_followup_items",
            validation=validation,
            human_audit_errors=["needs_followup_items must be resolved before saving note_correction_review"],
            review_storage_schema="not_checked",
        )

    db_path = Path(research_db_path)
    readiness = build_note_correction_review_save_readiness(research_db_path=db_path)
    request_gate = build_note_correction_review_save_request_gate(
        research_db_path=db_path,
        document_id=document_id,
        chapter_id=chapter_id,
        review_mode=review_mode,
        canary_subscope=canary_subscope,
        package=package,
        selected_server_note_ids=selected_server_note_ids,
        selected_note_ids=selected_note_ids,
        confirm_write=confirm_write,
        confirmation_context=confirmation_context,
        validator_valid=bool(validation.get("valid")),
        human_audit_confirmed=(
            bool(audit_result["items"])
            and audit_result["confirmed_count"] == len(audit_result["items"])
        ),
        validation=validation,
        readiness=readiness,
    )
    if not request_gate["allowed"]:
        blocked_readiness = {
            **readiness,
            "current_blockers": list(request_gate.get("current_blockers") or []),
        }
        return _blocked_save_response(
            document_id,
            chapter_id,
            str(request_gate.get("reason") or "production_review_write_not_allowed"),
            validation=validation,
            review_storage_schema=str(readiness.get("review_storage_schema") or "not_checked"),
            readiness=blocked_readiness,
            request_gate=request_gate,
        )

    conn = _connect_rw_existing(db_path)
    try:
        conn.row_factory = sqlite3.Row
        if not _note_correction_review_schema_ready(conn):
            return _blocked_save_response(
                document_id,
                chapter_id,
                "review_schema_missing",
                validation=validation,
                review_storage_schema="missing",
            )

        existing = _active_correction_review_row(
            conn,
            document_id=document_id,
            chapter_id=chapter_id,
            review_mode=review_mode,
            scope_id=scope_id,
            batch_size=batch_size,
            batch_index=batch_index,
        )
        if existing and not supersede_existing:
            flags = review_pipeline_safety_flags()
            return {
                "status": "duplicate_review_exists",
                "mode": "note_correction_review_save",
                "schema_version": NOTE_CORRECTION_REVIEW_SAVE_SCHEMA_VERSION,
                "human_audit_schema_version": NOTE_CORRECTION_HUMAN_AUDIT_SCHEMA_VERSION,
                "review_type": "note_correction_review",
                "document_id": document_id,
                "chapter_id": chapter_id,
                "review_mode": review_mode,
                "scope_id": scope_id,
                "valid": True,
                "reason": "duplicate_or_existing_scope",
                "review_id": existing["review_id"],
                "saved_item_count": 0,
                "skipped_existing_count": 1,
                "superseded_count": 0,
                "blocked_count": 0,
                "review_storage_schema": "present",
                "required_confirmation_context": NOTE_CORRECTION_SAVE_CONTEXT,
                "validation": validation,
                "production_canary_gate": request_gate,
                **_canary_subscope_response_metadata(package, validation),
                "ready_for_note_classification": False,
                "ready_for_zotero_writeback_queue": False,
                "safety_flags": flags,
                **flags,
            }

        normalized_review_json = _normalized_review_root(validation)
        summary_json = normalized_review_json["note_correction_review"].get("summary") or {}
        completeness_json = validation.get("completeness") or {}
        action_counts = dict(Counter(item["human_action"] for item in audit_result["items"]))
        audit_trace = _build_note_correction_save_audit_trace(
            document_id=document_id,
            chapter_id=chapter_id,
            review_mode=review_mode,
            scope_id=scope_id,
            batch_size=batch_size,
            batch_index=batch_index,
            confirmation_context=confirmation_context,
            action_counts=action_counts,
            audit_result=audit_result,
            supersede_existing=supersede_existing,
            existing_review_id=existing["review_id"] if existing else None,
        )
        safety_flags = review_pipeline_safety_flags(
            db_write_performed=True,
            schema_version=NOTE_CORRECTION_REVIEW_SAVE_SCHEMA_VERSION,
            human_audit_schema_version=NOTE_CORRECTION_HUMAN_AUDIT_SCHEMA_VERSION,
            audit_trace=audit_trace,
        )
        now = _utc_now()
        review_id = f"ncr_{uuid4().hex}"
        superseded_count = 0

        if existing and supersede_existing:
            conn.execute(
                f"""
                UPDATE {NOTE_CORRECTION_REVIEW_TABLE}
                SET review_status = 'superseded', updated_at = ?
                WHERE review_id = ?
                """,
                (now, existing["review_id"]),
            )
            superseded_count = 1
        conn.execute(
            f"""
            INSERT INTO {NOTE_CORRECTION_REVIEW_TABLE} (
                review_id, document_id, chapter_id, review_mode, scope_id,
                batch_size, batch_index, source_package_hash, normalized_review_json,
                review_summary_json, completeness_json, merge_scope_json,
                created_at, updated_at, created_by, review_status,
                confirmation_context, safety_flags_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                review_id,
                document_id,
                chapter_id,
                review_mode,
                scope_id,
                batch_size,
                batch_index,
                source_package_hash,
                json.dumps(normalized_review_json, ensure_ascii=False, sort_keys=True),
                json.dumps(summary_json, ensure_ascii=False, sort_keys=True),
                json.dumps(completeness_json, ensure_ascii=False, sort_keys=True),
                json.dumps(merge_preview, ensure_ascii=False, sort_keys=True) if merge_preview is not None else None,
                now,
                now,
                "user",
                "saved",
                confirmation_context,
                json.dumps(safety_flags, ensure_ascii=False, sort_keys=True),
            ),
        )
        for item in audit_result["items"]:
            conn.execute(
                f"""
                INSERT INTO {NOTE_CORRECTION_REVIEW_ITEM_TABLE} (
                    review_item_id, review_id, server_note_id, client_note_id,
                    zotero_annotation_key, page, original_note_text, selected_text,
                    ai_correction_status, ai_issue_type, ai_explanation,
                    ai_suggested_revision, ai_evidence_support, ai_confidence,
                    ai_reviewer_warning, human_action, final_note_text,
                    confirmed_by_user, writeback_intent, writeback_status,
                    writeback_target, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"ncri_{uuid4().hex}",
                    review_id,
                    item["server_note_id"],
                    item.get("client_note_id"),
                    item.get("zotero_annotation_key"),
                    item.get("page"),
                    item["original_note_text"],
                    item.get("selected_text"),
                    item["ai_correction_status"],
                    item["ai_issue_type"],
                    item["ai_explanation"],
                    item.get("ai_suggested_revision"),
                    item["ai_evidence_support"],
                    item.get("ai_confidence"),
                    item.get("ai_reviewer_warning"),
                    item["human_action"],
                    item.get("final_note_text"),
                    1 if item.get("confirmed_by_user") else 0,
                    item["writeback_intent"],
                    item["writeback_status"],
                    item.get("writeback_target"),
                    now,
                    now,
                ),
            )
        conn.commit()
    finally:
        conn.close()

    has_followup = action_counts.get("needs_followup", 0) > 0
    all_confirmed = all(bool(item.get("confirmed_by_user")) for item in audit_result["items"])
    merge_complete = _merge_scope_complete(review_mode=review_mode, merge_preview=merge_preview)
    ready_for_note_classification = all_confirmed and not has_followup and merge_complete
    ready_for_zotero_writeback_queue = any(
        item.get("confirmed_by_user") and item.get("writeback_intent") == "planned"
        for item in audit_result["items"]
    )
    note_classification_gate = "ready"
    if not ready_for_note_classification:
        if has_followup:
            note_classification_gate = "blocked needs_followup_items"
        elif not all_confirmed:
            note_classification_gate = "blocked unconfirmed_items"
        elif not merge_complete:
            note_classification_gate = "blocked note_correction_review_merge_incomplete"
    return {
        "status": "saved",
        "mode": "note_correction_review_save",
        "schema_version": NOTE_CORRECTION_REVIEW_SAVE_SCHEMA_VERSION,
        "human_audit_schema_version": NOTE_CORRECTION_HUMAN_AUDIT_SCHEMA_VERSION,
        "review_type": "note_correction_review",
        "document_id": document_id,
        "chapter_id": chapter_id,
        "review_mode": review_mode,
        "scope_id": scope_id,
        "batch_size": batch_size,
        "batch_index": batch_index,
        "valid": True,
        "reason": "saved_after_user_audit",
        "review_id": review_id,
        "saved_item_count": len(audit_result["items"]),
        "human_action_counts": action_counts,
        "audit_trace": audit_trace,
        "ready_for_note_classification": ready_for_note_classification,
        "note_classification_gate": note_classification_gate,
        "ready_for_zotero_writeback_queue": ready_for_zotero_writeback_queue,
        "inserted_count": 1,
        "skipped_existing_count": 0,
        "superseded_count": superseded_count,
        "blocked_count": 0,
        "review_storage_schema": "present",
        "required_confirmation_context": NOTE_CORRECTION_SAVE_CONTEXT,
        "validation": validation,
        "production_canary_gate": request_gate,
        **_canary_subscope_response_metadata(package, validation),
        "safety_flags": safety_flags,
        **safety_flags,
    }


def _blocked_save_response(
    document_id: int,
    chapter_id: int,
    reason: str,
    *,
    validation: Mapping[str, Any],
    review_storage_schema: str | None = None,
    human_audit_errors: list[str] | None = None,
    readiness: Mapping[str, Any] | None = None,
    request_gate: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    flags = review_pipeline_safety_flags()
    current_blockers = list((readiness or {}).get("current_blockers") or [])
    return {
        "status": "blocked",
        "mode": "note_correction_review_save",
        "schema_version": NOTE_CORRECTION_REVIEW_SAVE_SCHEMA_VERSION,
        "human_audit_schema_version": NOTE_CORRECTION_HUMAN_AUDIT_SCHEMA_VERSION,
        "review_type": "note_correction_review",
        "document_id": document_id,
        "chapter_id": chapter_id,
        "valid": bool(validation.get("valid")),
        "reason": reason,
        "inserted_count": 0,
        "skipped_existing_count": 0,
        "blocked_count": 1,
        "review_storage_schema": review_storage_schema or "not_checked",
        "required_confirmation_context": NOTE_CORRECTION_SAVE_CONTEXT,
        "audit_trace": {
            "schema_version": NOTE_CORRECTION_REVIEW_SAVE_SCHEMA_VERSION,
            "event": "note_correction_review_save_blocked",
            "reason": reason,
        },
        "validation": validation,
        "human_audit_errors": human_audit_errors or [],
        "production_canary_gate": dict(request_gate or {}),
        "saved_item_count": 0,
        "human_action_counts": {},
        "ready_for_note_classification": False,
        "ready_for_zotero_writeback_queue": False,
        "review_schema_ready": bool((readiness or {}).get("review_schema_ready", False)),
        "production_review_write_allowed": bool((readiness or {}).get("production_review_write_allowed", False)),
        "production_db_write_enabled": bool((readiness or {}).get("production_db_write_enabled", False)),
        "save_endpoint_available": bool((readiness or {}).get("save_endpoint_available", True)),
        "current_blockers": current_blockers,
        "safety_flags": flags,
        **flags,
    }


def _blocked_canary_plan_response(
    *,
    document_id: int,
    chapter_id: int,
    reason: str,
    preflight: Mapping[str, Any],
    before_snapshot: Mapping[str, Any],
    validation: Mapping[str, Any],
    human_audit_errors: list[str] | None = None,
) -> dict[str, Any]:
    after_snapshot = build_note_correction_production_db_snapshot(
        research_db_path=preflight.get("db_path") or DEFAULT_DB_PATH
    )
    flags = review_pipeline_safety_flags()
    return {
        "status": "blocked",
        "mode": "note_correction_review_save_canary_plan",
        "document_id": document_id,
        "chapter_id": chapter_id,
        "reason": reason,
        "schema_version": NOTE_CORRECTION_REVIEW_SAVE_SCHEMA_VERSION,
        "human_audit_schema_version": NOTE_CORRECTION_HUMAN_AUDIT_SCHEMA_VERSION,
        "expected_item_count": int((validation.get("completeness") or {}).get("expected_count") or 0),
        "actual_item_count": int((validation.get("completeness") or {}).get("actual_count") or 0),
        "final_note_text_count": 0,
        "confirmed_item_count": 0,
        "human_action_counts": {},
        "audit_trace_summary": {
            "schema_version": NOTE_CORRECTION_REVIEW_SAVE_SCHEMA_VERSION,
            "human_audit_schema_version": NOTE_CORRECTION_HUMAN_AUDIT_SCHEMA_VERSION,
            "event": "note_correction_review_save_canary_plan_blocked",
            "reason": reason,
            "no_write": True,
        },
        "supersede_target": None,
        "duplicate_target": None,
        "planned_review_write_count": 0,
        "planned_item_write_count": 0,
        "execution_allowed": False,
        "current_blockers": list(preflight.get("current_blockers") or []),
        "human_audit_errors": human_audit_errors or [],
        "production_canary_preflight": dict(preflight),
        "db_snapshot_before": dict(before_snapshot),
        "db_snapshot_after": after_snapshot,
        "db_snapshot_unchanged": before_snapshot.get("db_hash_sha256") == after_snapshot.get("db_hash_sha256")
        and before_snapshot.get("counts") == after_snapshot.get("counts"),
        "validation": validation,
        "no_write": True,
        "canary_plan_only": True,
        "real_save_api_called": False,
        "safety_flags": flags,
        **flags,
    }


def _expected_correction_package(
    *,
    research_db_path: str | Path,
    document_id: int,
    chapter_id: int,
    review_mode: str,
    scope_id: str | None,
    batch_size: int | None,
    batch_index: int | None,
    parent_review_mode: str | None = None,
    parent_scope_id: str | None = None,
    selected_server_note_ids: list[str] | None = None,
    selected_note_ids: list[str] | None = None,
) -> dict[str, Any]:
    if review_mode == "canary_subscope":
        return build_chapter_note_correction_canary_subscope_package(
            research_db_path=research_db_path,
            document_id=document_id,
            chapter_id=chapter_id,
            parent_review_mode=parent_review_mode or "section_scoped",
            parent_scope_id=parent_scope_id or scope_id,
            selected_server_note_ids=selected_server_note_ids,
            selected_note_ids=selected_note_ids,
        )
    if review_mode == "section_scoped":
        return build_chapter_note_correction_scoped_package(
            research_db_path=research_db_path,
            document_id=document_id,
            chapter_id=chapter_id,
            review_mode="section_scoped",
            section_id=scope_id,
        )
    if review_mode == "fixed_size_batch":
        return build_chapter_note_correction_scoped_package(
            research_db_path=research_db_path,
            document_id=document_id,
            chapter_id=chapter_id,
            review_mode="fixed_size_batch",
            batch_size=int(batch_size or 15),
            batch_index=int(batch_index or 0),
        )
    if review_mode != "full_chapter":
        raise ChapterNoteCorrectionPromptError(f"unsupported note correction review mode: {review_mode}")
    return build_chapter_note_correction_scoped_package(
        research_db_path=research_db_path,
        document_id=document_id,
        chapter_id=chapter_id,
        review_mode="full_chapter",
    )


def _build_note_correction_save_audit_trace(
    *,
    document_id: int,
    chapter_id: int,
    review_mode: str,
    scope_id: str | None,
    batch_size: int | None,
    batch_index: int | None,
    confirmation_context: str | None,
    action_counts: Mapping[str, int],
    audit_result: Mapping[str, Any],
    supersede_existing: bool,
    existing_review_id: str | None,
    event: str = "note_correction_review_saved_after_user_audit",
    no_write: bool = False,
) -> dict[str, Any]:
    return {
        "schema_version": NOTE_CORRECTION_REVIEW_SAVE_SCHEMA_VERSION,
        "human_audit_schema_version": NOTE_CORRECTION_HUMAN_AUDIT_SCHEMA_VERSION,
        "event": event,
        "document_id": document_id,
        "chapter_id": chapter_id,
        "review_mode": review_mode,
        "scope_id": scope_id,
        "batch_size": batch_size,
        "batch_index": batch_index,
        "confirmation_context": confirmation_context,
        "required_confirmation_context": NOTE_CORRECTION_SAVE_CONTEXT,
        "total_items": len(audit_result.get("items") or []),
        "confirmed_items": int(audit_result.get("confirmed_count") or 0),
        "final_note_text_saved_count": int(audit_result.get("final_note_text_count") or 0),
        "human_action_counts": dict(action_counts),
        "supersede_existing": bool(supersede_existing),
        "superseded_review_id": existing_review_id if supersede_existing else None,
        "no_write": bool(no_write),
        "real_save_api_called": False,
        "zotero_db_write_performed": False,
        "vector_store_write_performed": False,
        "llm_called": False,
        "object_candidates_generated": False,
        "relation_generated": False,
        "mechanism_generated": False,
    }


_PIPELINE_EXPORTS = {
    "build_chapter_note_classification_dry_run_package": "classification",
    "build_chapter_note_classification_package": "classification",
    "build_chapter_object_candidate_dry_run_package": "object_candidates",
    "build_chapter_relation_candidate_dry_run_package": "relations",
    "build_saved_note_correction_review_state": "loading",
    "build_tri_source_object_package_preview": "object_candidates",
    "save_chapter_note_classification_manual_json": "classification_persistence",
    "save_chapter_object_candidate_dry_run_drafts": "object_draft_review",
    "save_object_candidate_human_review": "object_human_review",
}


def __getattr__(name: str) -> Any:
    module_name = _PIPELINE_EXPORTS.get(name)
    if module_name:
        from importlib import import_module

        module = import_module(f"app.domains.chapter_review.{module_name}")
        return getattr(module, name)
    raise AttributeError(name)
