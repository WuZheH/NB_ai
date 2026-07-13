"""Chapter review classification persistence responsibilities."""

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

from .classification import (
    build_chapter_note_classification_dry_run_package,
)

from .classification_validation import (
    _manual_forbidden_keys,
    _parse_manual_classification_payload,
    validate_chapter_note_classification_manual_json,
)

from .loading import (
    load_merged_saved_note_correction_review,
    load_saved_note_classification_review,
)

from .normalization import (
    _hash_json_for_contract,
    _hash_review,
    _int_or_none,
    _utc_now,
)

from .persistence import (
    _connect_rw_existing,
    _latest_saved_classification_review_row,
    _note_classification_review_schema_ready,
)

from .safety import (
    review_pipeline_safety_flags,
)

from .schema import (
    build_note_classification_review_schema_audit,
)

from .write_policy import (
    is_production_note_classification_save_enabled,
)

def build_note_classification_review_save_readiness(
    *,
    research_db_path: str | Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    db_path = Path(research_db_path)
    schema_audit = build_note_classification_review_schema_audit(research_db_path=db_path)
    blockers: list[str] = []
    if not schema_audit.get("schema_ready"):
        blockers.append("note_classification_review_schema_missing")
    if not is_production_note_classification_save_enabled():
        blockers.append("production_note_classification_save_disabled")
    flags = review_pipeline_safety_flags()
    return {
        "status": "ok",
        "mode": "r3_phase7c_note_classification_review_save_readiness",
        "db_path": str(db_path),
        "schema_ready": bool(schema_audit.get("schema_ready")),
        "schema_version": NOTE_CLASSIFICATION_REVIEW_SAVE_SCHEMA_VERSION,
        "production_note_classification_save_env": PRODUCTION_NOTE_CLASSIFICATION_SAVE_ENV,
        "production_note_classification_save_enabled": is_production_note_classification_save_enabled(),
        "production_note_classification_write_allowed": not blockers,
        "write_available": not blockers,
        "required_confirmation_context": NOTE_CLASSIFICATION_SAVE_CONTEXT,
        "allowed_document_id": PRODUCTION_REVIEW_SECTION_DOCUMENT_ID,
        "allowed_chapter_id": PRODUCTION_REVIEW_SECTION_CHAPTER_ID,
        "allowed_review_mode": "manual_json",
        "required_item_count": 67,
        "allowed_write_tables": list(PRODUCTION_NOTE_CLASSIFICATION_WRITE_TABLES),
        "current_blockers": blockers,
        "schema_audit": schema_audit,
        "safety_flags": flags,
        **flags,
    }


def build_note_classification_review_save_request_gate(
    *,
    research_db_path: str | Path,
    document_id: int,
    chapter_id: int,
    classification_payload: str | Mapping[str, Any] | list[Any],
    validation: Mapping[str, Any],
    confirm_write: bool,
    confirmation_context: str | None,
    readiness: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    db_path = Path(research_db_path)
    resolved_readiness = dict(
        readiness
        or build_note_classification_review_save_readiness(research_db_path=db_path)
    )
    blockers: list[str] = list(resolved_readiness.get("current_blockers") or [])
    package = build_chapter_note_classification_dry_run_package(
        research_db_path=db_path,
        document_id=document_id,
        chapter_id=chapter_id,
    )
    parsed_errors: list[str] = []
    parsed = _parse_manual_classification_payload(classification_payload, parsed_errors)
    stats = validation.get("stats") or {}
    pn68 = validation.get("pn68_validation") or {}
    pn68_item = pn68.get("item") or {}
    pn68_warnings = [str(item).lower() for item in (pn68_item.get("warnings") or [])]

    if parsed_errors:
        blockers.append("classification_payload_parse_failed")
    if int(document_id) != PRODUCTION_REVIEW_SECTION_DOCUMENT_ID:
        blockers.append("production_note_classification_document_id_required")
    if int(chapter_id) != PRODUCTION_REVIEW_SECTION_CHAPTER_ID:
        blockers.append("production_note_classification_chapter_id_required")
    if not package.get("ready"):
        blockers.append(str(package.get("reason") or "classification_package_not_ready"))
    if validation.get("valid") is not True:
        blockers.append("manual_classification_validation_failed")
    if not confirm_write:
        blockers.append("confirm_write_required")
    if confirmation_context != NOTE_CLASSIFICATION_SAVE_CONTEXT:
        blockers.append("confirmation_context_invalid")
    if validation.get("source_package_hash_status") != "matched":
        blockers.append("source_package_hash_required_and_matched")
    if int(package.get("item_count") or 0) != 67:
        blockers.append("source_merged_correction_review_item_count_mismatch")
    if int(stats.get("expected_item_count") or 0) != 67 or int(stats.get("item_count") or 0) != 67:
        blockers.append("manual_classification_item_count_mismatch")
    for key in ["missing_count", "duplicate_count", "unexpected_count", "invalid_label_count", "invalid_confidence_count", "invalid_item_count"]:
        if int(stats.get(key) or 0) != 0:
            blockers.append(f"manual_classification_{key}_must_be_zero")
    if pn68.get("valid") is not True:
        blockers.append("pn68_classification_warning_not_preserved")
    if pn68_item.get("note_type") not in {"needs_manual_review", "unclear"}:
        blockers.append("pn68_classification_label_requires_manual_review_or_unclear")
    if not any("alignment_uncertain" in item or "unmatched" in item for item in pn68_warnings):
        blockers.append("pn68_alignment_warning_required")
    if _classification_payload_requests_forbidden_side_effects(parsed):
        blockers.append("classification_payload_side_effect_fields_forbidden")
    if any(
        bool(validation.get(key))
        for key in [
            "llm_called",
            "object_candidates_generated",
            "relation_generated",
            "mechanism_generated",
            "zotero_write_performed",
            "vector_write_performed",
            "generation_performed",
        ]
    ):
        blockers.append("classification_validation_side_effect_flags_forbidden")

    existing = load_saved_note_classification_review(
        research_db_path=db_path,
        document_id=document_id,
        chapter_id=chapter_id,
    )
    if existing:
        blockers.append("note_classification_review_already_saved")

    blockers = list(dict.fromkeys(blockers))
    return {
        "allowed": not blockers,
        "reason": blockers[0] if blockers else None,
        "mode": "production_note_classification_manual_json_save",
        "db_path": str(db_path),
        "document_id": document_id,
        "chapter_id": chapter_id,
        "classification_env": PRODUCTION_NOTE_CLASSIFICATION_SAVE_ENV,
        "classification_env_enabled": is_production_note_classification_save_enabled(),
        "required_confirmation_context": NOTE_CLASSIFICATION_SAVE_CONTEXT,
        "request_confirm_write": bool(confirm_write),
        "request_confirmation_context": confirmation_context,
        "source_package_hash_status": validation.get("source_package_hash_status"),
        "expected_item_count": int(stats.get("expected_item_count") or 0),
        "actual_item_count": int(stats.get("item_count") or 0),
        "pn68_status": pn68,
        "existing_review_id": existing.get("review_id") if existing else None,
        "allowed_write_tables": list(PRODUCTION_NOTE_CLASSIFICATION_WRITE_TABLES),
        "current_blockers": blockers,
    }


def save_chapter_note_classification_manual_json(
    *,
    research_db_path: str | Path = DEFAULT_DB_PATH,
    document_id: int,
    chapter_id: int,
    classification_payload: str | Mapping[str, Any] | list[Any],
    confirm_write: bool,
    confirmation_context: str | None,
) -> dict[str, Any]:
    db_path = Path(research_db_path)
    validation = validate_chapter_note_classification_manual_json(
        research_db_path=db_path,
        document_id=document_id,
        chapter_id=chapter_id,
        classification_payload=classification_payload,
    )
    readiness = build_note_classification_review_save_readiness(research_db_path=db_path)
    request_gate = build_note_classification_review_save_request_gate(
        research_db_path=db_path,
        document_id=document_id,
        chapter_id=chapter_id,
        classification_payload=classification_payload,
        validation=validation,
        confirm_write=confirm_write,
        confirmation_context=confirmation_context,
        readiness=readiness,
    )
    if not request_gate["allowed"]:
        return _blocked_classification_save_response(
            document_id=document_id,
            chapter_id=chapter_id,
            reason=str(request_gate.get("reason") or "note_classification_review_save_blocked"),
            validation=validation,
            readiness=readiness,
            request_gate=request_gate,
        )

    package = build_chapter_note_classification_dry_run_package(
        research_db_path=db_path,
        document_id=document_id,
        chapter_id=chapter_id,
    )
    parsed_errors: list[str] = []
    parsed = _parse_manual_classification_payload(classification_payload, parsed_errors)
    if parsed_errors or not isinstance(parsed, Mapping):
        return _blocked_classification_save_response(
            document_id=document_id,
            chapter_id=chapter_id,
            reason="classification_payload_parse_failed",
            validation=validation,
            readiness=readiness,
            request_gate=request_gate,
        )
    candidates_by_server_id = {
        str(candidate.get("server_note_id") or "").strip(): candidate
        for candidate in (package.get("classification_candidates") or [])
        if str(candidate.get("server_note_id") or "").strip()
    }
    normalized_items = list(validation.get("preview_items") or [])
    label_counts = dict(validation.get("label_counts") or {})
    confidence_counts = dict(validation.get("confidence_counts") or {})
    pn68 = dict(validation.get("pn68_validation") or {})
    safety_flags = review_pipeline_safety_flags(
        db_write_performed=True,
        schema_version=NOTE_CLASSIFICATION_REVIEW_SAVE_SCHEMA_VERSION,
        classification_review_save=True,
    )
    now = _utc_now()
    review_id = f"nclr_{uuid4().hex}"
    canonical_payload = json.dumps(parsed, ensure_ascii=False, sort_keys=True, default=str)
    review_hash = _hash_review(
        NOTE_CLASSIFICATION_REVIEW_SAVE_SCHEMA_VERSION,
        str(package.get("source_package_hash") or ""),
        canonical_payload,
    )
    merged = load_merged_saved_note_correction_review(
        research_db_path=db_path,
        document_id=document_id,
        chapter_id=chapter_id,
    ) or {}
    source_merged_review_hash = _hash_json_for_contract(merged) if merged else None

    conn = _connect_rw_existing(db_path)
    try:
        conn.row_factory = sqlite3.Row
        if not _note_classification_review_schema_ready(conn):
            return _blocked_classification_save_response(
                document_id=document_id,
                chapter_id=chapter_id,
                reason="note_classification_review_schema_missing",
                validation=validation,
                readiness=readiness,
                request_gate=request_gate,
            )
        if _latest_saved_classification_review_row(conn, document_id=document_id, chapter_id=chapter_id):
            return _blocked_classification_save_response(
                document_id=document_id,
                chapter_id=chapter_id,
                reason="note_classification_review_already_saved",
                validation=validation,
                readiness=readiness,
                request_gate={**request_gate, "allowed": False, "current_blockers": ["note_classification_review_already_saved"]},
            )
        conn.execute(
            f"""
            INSERT INTO {NOTE_CLASSIFICATION_REVIEW_TABLE} (
                review_id, document_id, chapter_id, review_type, review_mode,
                source_package_hash, source_merged_review_hash, source_item_count,
                review_hash, review_json, normalized_items_json, stats_json,
                label_counts_json, confidence_counts_json, pn68_validation_json,
                confirmation_context, status, validation_status,
                ready_for_object_candidate_generation, llm_called, db_write_performed,
                zotero_write_performed, vector_write_performed,
                object_candidates_generated, relation_generated, mechanism_generated,
                safety_flags_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                review_id,
                document_id,
                chapter_id,
                "note_classification_review",
                "manual_json",
                str(package.get("source_package_hash") or ""),
                source_merged_review_hash,
                int(package.get("item_count") or 0),
                review_hash,
                canonical_payload,
                json.dumps(normalized_items, ensure_ascii=False, sort_keys=True, default=str),
                json.dumps(validation.get("stats") or {}, ensure_ascii=False, sort_keys=True, default=str),
                json.dumps(label_counts, ensure_ascii=False, sort_keys=True, default=str),
                json.dumps(confidence_counts, ensure_ascii=False, sort_keys=True, default=str),
                json.dumps(pn68, ensure_ascii=False, sort_keys=True, default=str),
                confirmation_context,
                "saved",
                "valid",
                1,
                0,
                1,
                0,
                0,
                0,
                0,
                0,
                json.dumps(safety_flags, ensure_ascii=False, sort_keys=True, default=str),
                now,
                now,
            ),
        )
        for item in normalized_items:
            server_note_id = str(item.get("server_note_id") or "").strip()
            candidate = candidates_by_server_id.get(server_note_id) or {}
            special_flags = {
                "warnings": item.get("warnings") or [],
                "pn68": server_note_id == PRODUCTION_REVIEW_SECTION84_PN68_SERVER_NOTE_ID,
                "preserve_original_note_text": True,
                "matched_chunk_id": candidate.get("matched_chunk_id"),
            }
            conn.execute(
                f"""
                INSERT INTO {NOTE_CLASSIFICATION_REVIEW_ITEM_TABLE} (
                    review_item_id, review_id, server_note_id, client_note_id,
                    zotero_annotation_key, section_id, page_label, pdf_page,
                    original_note_text, selected_text, classification_label,
                    confidence, rationale, evidence_alignment_status,
                    special_flags_json, source_correction_item_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"nclri_{uuid4().hex}",
                    review_id,
                    server_note_id,
                    candidate.get("client_note_id"),
                    candidate.get("zotero_annotation_key") or item.get("zotero_annotation_key"),
                    candidate.get("source_section_id") or item.get("source_section_id"),
                    str(candidate.get("page") or item.get("page") or "") or None,
                    _int_or_none(candidate.get("page") or item.get("page")),
                    str(candidate.get("original_note_text") or ""),
                    candidate.get("selected_text") or "",
                    item.get("note_type"),
                    item.get("confidence"),
                    item.get("rationale") or "",
                    "alignment_uncertain" if special_flags["pn68"] else str(candidate.get("anchor_method") or "not_recorded"),
                    json.dumps(special_flags, ensure_ascii=False, sort_keys=True, default=str),
                    candidate.get("source_review_id"),
                    now,
                ),
            )
        conn.commit()
    finally:
        conn.close()

    saved = load_saved_note_classification_review(
        research_db_path=db_path,
        document_id=document_id,
        chapter_id=chapter_id,
    )
    return {
        "status": "saved",
        "mode": "r3_phase7c_note_classification_manual_json_save",
        "schema_version": NOTE_CLASSIFICATION_REVIEW_SAVE_SCHEMA_VERSION,
        "review_type": "note_classification_review",
        "document_id": document_id,
        "chapter_id": chapter_id,
        "review_mode": "manual_json",
        "classification_review_id": review_id,
        "review_id": review_id,
        "saved_item_count": len(normalized_items),
        "source_item_count": int(package.get("item_count") or 0),
        "label_counts": label_counts,
        "confidence_counts": confidence_counts,
        "pn68_status": pn68,
        "db_write_performed": True,
        "llm_called": False,
        "zotero_write_performed": False,
        "vector_write_performed": False,
        "object_candidates_generated": False,
        "relation_generated": False,
        "mechanism_generated": False,
        "generation_performed": False,
        "ready_for_object_candidate_generation": True,
        "object_candidate_generation_status": "requires_explicit_phase7d_gate",
        "validation": validation,
        "classification_save_gate": request_gate,
        "saved_review": saved,
        "safety_flags": safety_flags,
        **safety_flags,
    }


def _blocked_classification_save_response(
    *,
    document_id: int,
    chapter_id: int,
    reason: str,
    validation: Mapping[str, Any] | None = None,
    readiness: Mapping[str, Any] | None = None,
    request_gate: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    flags = review_pipeline_safety_flags()
    return {
        "status": "blocked",
        "mode": "r3_phase7c_note_classification_manual_json_save",
        "schema_version": NOTE_CLASSIFICATION_REVIEW_SAVE_SCHEMA_VERSION,
        "review_type": "note_classification_review",
        "document_id": document_id,
        "chapter_id": chapter_id,
        "reason": reason,
        "classification_review_id": None,
        "review_id": None,
        "saved_item_count": 0,
        "db_write_performed": False,
        "llm_called": False,
        "zotero_write_performed": False,
        "vector_write_performed": False,
        "object_candidates_generated": False,
        "relation_generated": False,
        "mechanism_generated": False,
        "generation_performed": False,
        "validation": validation,
        "classification_save_readiness": readiness,
        "classification_save_gate": request_gate,
        "safety_flags": flags,
        **flags,
    }


def _classification_payload_requests_forbidden_side_effects(value: Any) -> bool:
    forbidden = _manual_forbidden_keys(value)
    if forbidden:
        return True
    if isinstance(value, Mapping):
        for key in [
            "llm_called",
            "zotero_write_performed",
            "vector_write_performed",
            "object_candidates_generated",
            "relation_generated",
            "mechanism_generated",
            "generation_performed",
        ]:
            if value.get(key) is True:
                return True
    return False
