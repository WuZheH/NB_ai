"""Chapter review gates responsibilities."""

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

from .normalization import (
    _int_or_none,
)

from .persistence import (
    _chapter_exists,
    _connect_ro_existing,
    _is_default_research_db_path,
    _note_correction_review_schema_ready,
)

from .safety import (
    review_pipeline_safety_flags,
)

from .write_policy import (
    is_production_review_save_canary_enabled,
    is_production_review_save_section84_pn68_enabled,
    is_production_review_save_section_enabled,
    production_review_save_section_target,
    production_review_save_section_target_expected_count,
)

def build_note_correction_review_save_readiness(
    *,
    research_db_path: str | Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    db_path = Path(research_db_path)
    flags = review_pipeline_safety_flags()
    blockers: list[str] = []
    review_schema_ready = False
    review_storage_schema = "missing"

    try:
        conn = _connect_ro_existing(db_path)
        try:
            review_schema_ready = _note_correction_review_schema_ready(conn)
            review_storage_schema = "present" if review_schema_ready else "missing"
        finally:
            conn.close()
    except sqlite3.Error:
        blockers.append("review_db_unavailable")

    if not review_schema_ready and "review_schema_missing" not in blockers:
        blockers.append("review_schema_missing")

    is_production_db = _is_default_research_db_path(db_path)
    production_db_write_enabled = PRODUCTION_DB_WRITE_ENABLED if is_production_db else True
    production_review_canary_mode_enabled = is_production_review_save_canary_enabled()
    production_review_section_mode_enabled = is_production_review_save_section_enabled()
    production_review_section84_pn68_mode_enabled = is_production_review_save_section84_pn68_enabled()
    production_review_section_target = production_review_save_section_target()
    production_review_section_target_expected_count = (
        production_review_save_section_target_expected_count(production_review_section_target)
    )
    production_review_canary_mode_available = (
        is_production_db
        and production_review_canary_mode_enabled
        and review_schema_ready
    )
    production_review_section_mode_available = (
        is_production_db
        and production_review_section_mode_enabled
        and review_schema_ready
    )
    production_review_section84_pn68_mode_available = (
        is_production_db
        and production_review_section84_pn68_mode_enabled
        and review_schema_ready
    )
    if is_production_db and not production_db_write_enabled:
        if production_review_section84_pn68_mode_enabled:
            if not production_review_section_target:
                blockers.append("production_section84_pn68_target_required")
            elif production_review_section_target != PRODUCTION_REVIEW_SECTION84_PN68_SCOPE_ID:
                blockers.append("production_section84_pn68_target_required")
            else:
                blockers.append("production_section84_pn68_request_context_required")
        elif production_review_canary_mode_enabled and production_review_section_mode_enabled:
            blockers.append("production_review_request_context_required")
        elif production_review_section_mode_enabled:
            if not production_review_section_target:
                blockers.append("production_section_target_required")
            elif production_review_section_target in PRODUCTION_REVIEW_SECTION_DEFERRED_SCOPES:
                blockers.append("production_section_target_deferred")
            elif production_review_section_target_expected_count is None:
                blockers.append("production_section_target_not_allowed")
            else:
                blockers.append("production_section_request_context_required")
        elif production_review_canary_mode_enabled:
            blockers.append("production_canary_request_context_required")
        else:
            blockers.append("production_db_write_disabled")

    blockers = list(dict.fromkeys(blockers))
    production_review_write_allowed = review_schema_ready and not blockers
    return {
        "status": "ok",
        "mode": "note_correction_review_save_readiness",
        "review_schema_ready": review_schema_ready,
        "review_storage_schema": review_storage_schema,
        "production_review_write_allowed": production_review_write_allowed,
        "production_db_write_enabled": production_db_write_enabled,
        "write_available": production_review_write_allowed,
        "production_review_canary_env": PRODUCTION_REVIEW_SAVE_CANARY_ENV,
        "production_review_canary_mode_enabled": production_review_canary_mode_enabled,
        "production_review_canary_mode_available": production_review_canary_mode_available,
        "production_review_canary_requires_request_context": True,
        "production_review_canary_allowed_review_mode": "canary_subscope",
        "production_review_canary_selected_count_min": 1,
        "production_review_canary_selected_count_max": 3,
        "production_review_canary_write_tables": list(PRODUCTION_REVIEW_CANARY_WRITE_TABLES),
        "production_review_section_env": PRODUCTION_REVIEW_SAVE_SECTION_ENV,
        "production_review_section_target_env": PRODUCTION_REVIEW_SAVE_SECTION_TARGET_ENV,
        "production_review_section_target": production_review_section_target or None,
        "production_review_section_mode_enabled": production_review_section_mode_enabled,
        "production_review_section_mode_available": production_review_section_mode_available,
        "production_review_section_requires_request_context": True,
        "production_review_section_allowed_document_id": PRODUCTION_REVIEW_SECTION_DOCUMENT_ID,
        "production_review_section_allowed_chapter_id": PRODUCTION_REVIEW_SECTION_CHAPTER_ID,
        "production_review_section_allowed_review_mode": "section_scoped",
        "production_review_section_allowed_scope_ids": list(PRODUCTION_REVIEW_SECTION_ALLOWED_SCOPES),
        "production_review_section_deferred_scope_ids": dict(PRODUCTION_REVIEW_SECTION_DEFERRED_SCOPES),
        "production_review_section_target_expected_count": production_review_section_target_expected_count,
        "production_review_section_write_tables": list(PRODUCTION_REVIEW_CANARY_WRITE_TABLES),
        "production_review_section84_pn68_env": PRODUCTION_REVIEW_SAVE_SECTION84_PN68_ENV,
        "production_review_section84_pn68_mode_enabled": production_review_section84_pn68_mode_enabled,
        "production_review_section84_pn68_mode_available": production_review_section84_pn68_mode_available,
        "production_review_section84_pn68_requires_request_context": True,
        "production_review_section84_pn68_allowed_scope_id": PRODUCTION_REVIEW_SECTION84_PN68_SCOPE_ID,
        "production_review_section84_pn68_expected_count": PRODUCTION_REVIEW_SECTION84_PN68_EXPECTED_COUNT,
        "production_review_section84_pn68_server_note_id": PRODUCTION_REVIEW_SECTION84_PN68_SERVER_NOTE_ID,
        "production_review_section84_pn68_zotero_key": PRODUCTION_REVIEW_SECTION84_PN68_ZOTERO_KEY,
        "production_review_section84_pn68_write_tables": list(PRODUCTION_REVIEW_CANARY_WRITE_TABLES),
        "write_policy_source": "backend_boundary",
        "save_endpoint_available": True,
        "requires_confirm_write": True,
        "required_confirmation_context": NOTE_CORRECTION_SAVE_CONTEXT,
        "schema_version": NOTE_CORRECTION_REVIEW_SAVE_SCHEMA_VERSION,
        "human_audit_schema_version": NOTE_CORRECTION_HUMAN_AUDIT_SCHEMA_VERSION,
        "current_blockers": blockers,
        "safety_flags": flags,
        **flags,
    }


def build_note_correction_review_save_request_gate(
    *,
    research_db_path: str | Path,
    document_id: int,
    chapter_id: int,
    review_mode: str,
    canary_subscope: bool,
    package: Mapping[str, Any],
    selected_server_note_ids: list[str] | None,
    selected_note_ids: list[str] | None,
    confirm_write: bool,
    confirmation_context: str | None,
    validator_valid: bool,
    human_audit_confirmed: bool,
    validation: Mapping[str, Any] | None = None,
    readiness: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    db_path = Path(research_db_path)
    resolved_readiness = dict(
        readiness
        or build_note_correction_review_save_readiness(research_db_path=db_path)
    )
    is_production_db = _is_default_research_db_path(db_path)
    selected = [
        str(value or "").strip()
        for value in (selected_server_note_ids or [])
        if str(value or "").strip()
    ]
    legacy_selected = [
        str(value or "").strip()
        for value in (selected_note_ids or [])
        if str(value or "").strip()
    ]
    scope = package.get("scope") or {}
    package_selected = [
        str(value or "").strip()
        for value in (scope.get("selected_server_note_ids") or [])
        if str(value or "").strip()
    ]
    candidate_server_ids = [
        str(candidate.get("server_note_id") or "").strip()
        for candidate in (package.get("correction_candidates") or [])
    ]
    blockers: list[str] = []

    if not is_production_db:
        if not resolved_readiness.get("production_review_write_allowed"):
            blockers.extend(resolved_readiness.get("current_blockers") or [])
        return {
            "allowed": not blockers,
            "reason": _review_save_block_reason({"current_blockers": blockers}) if blockers else None,
            "mode": "non_production_review_save",
            "is_production_db": False,
            "request_review_mode": review_mode,
            "request_canary_subscope": bool(canary_subscope),
            "selected_count": len(selected),
            "current_blockers": list(dict.fromkeys(blockers)),
            "allowed_write_tables": list(PRODUCTION_REVIEW_CANARY_WRITE_TABLES),
        }

    canary_enabled = is_production_review_save_canary_enabled()
    section_enabled = is_production_review_save_section_enabled()
    section84_pn68_enabled = is_production_review_save_section84_pn68_enabled()
    section_target = production_review_save_section_target()
    section_target_expected_count = production_review_save_section_target_expected_count(section_target)
    if PRODUCTION_DB_WRITE_ENABLED and resolved_readiness.get("production_review_write_allowed"):
        return {
            "allowed": True,
            "reason": None,
            "mode": "production_standard_review_save",
            "is_production_db": True,
            "request_review_mode": review_mode,
            "request_canary_subscope": bool(canary_subscope),
            "selected_count": len(selected),
            "current_blockers": [],
            "allowed_write_tables": list(PRODUCTION_REVIEW_CANARY_WRITE_TABLES),
        }

    if not canary_enabled and not section_enabled and not section84_pn68_enabled:
        return {
            "allowed": False,
            "reason": "production_db_write_disabled",
            "mode": "production_review_save_guarded",
            "is_production_db": True,
            "canary_env": PRODUCTION_REVIEW_SAVE_CANARY_ENV,
            "canary_env_enabled": False,
            "section_env": PRODUCTION_REVIEW_SAVE_SECTION_ENV,
            "section_env_enabled": False,
            "request_review_mode": review_mode,
            "request_canary_subscope": bool(canary_subscope),
            "selected_count": len(selected),
            "selected_server_note_ids": selected,
            "current_blockers": ["production_db_write_disabled"],
            "allowed_write_tables": list(PRODUCTION_REVIEW_CANARY_WRITE_TABLES),
        }
    if section84_pn68_enabled:
        return _build_note_correction_review_save_section84_pn68_request_gate(
            document_id=document_id,
            chapter_id=chapter_id,
            review_mode=review_mode,
            canary_subscope=canary_subscope,
            package=package,
            selected_server_note_ids=selected_server_note_ids,
            selected_note_ids=selected_note_ids,
            confirm_write=confirm_write,
            confirmation_context=confirmation_context,
            validator_valid=validator_valid,
            human_audit_confirmed=human_audit_confirmed,
            validation=validation,
            readiness=resolved_readiness,
        )
    if section_enabled and (review_mode == "section_scoped" or not canary_enabled):
        validation_payload = validation or {}
        completeness = validation_payload.get("completeness") or {}
        stats = validation_payload.get("stats") or {}
        expected_count = _int_or_none(completeness.get("expected_count"))
        actual_count = _int_or_none(completeness.get("actual_count"))
        scope_id = str(
            package.get("scope_id")
            or scope.get("scope_id")
            or scope.get("section_id")
            or ""
        ).strip()
        request_scope_id = str(scope_id or "").strip()
        package_section_id = str(scope.get("section_id") or "").strip()
        candidate_zotero_keys = [
            str(candidate.get("zotero_annotation_key") or "").strip()
            for candidate in (package.get("correction_candidates") or [])
        ]
        if not resolved_readiness.get("review_schema_ready"):
            blockers.append("review_schema_missing")
        if int(document_id) != PRODUCTION_REVIEW_SECTION_DOCUMENT_ID:
            blockers.append("production_section_document_id_required")
        if int(chapter_id) != PRODUCTION_REVIEW_SECTION_CHAPTER_ID:
            blockers.append("production_section_chapter_id_required")
        if review_mode != "section_scoped":
            blockers.append("production_section_review_mode_required")
        if canary_subscope:
            blockers.append("production_section_canary_subscope_forbidden")
        if not section_target:
            blockers.append("production_section_target_required")
        elif section_target in PRODUCTION_REVIEW_SECTION_DEFERRED_SCOPES:
            blockers.append("production_section_target_deferred")
        elif section_target_expected_count is None:
            blockers.append("production_section_target_not_allowed")
        if section_target and request_scope_id != section_target:
            blockers.append("production_section_scope_required")
        if section_target and package_section_id and package_section_id != section_target:
            blockers.append("production_section_package_scope_required")
        if legacy_selected:
            blockers.append("production_section_selected_note_ids_alias_forbidden")
        if selected and selected != candidate_server_ids:
            blockers.append("production_section_selected_server_note_ids_mismatch")
        if (
            section_target_expected_count is None
            or expected_count != section_target_expected_count
            or actual_count != section_target_expected_count
            or len(candidate_server_ids) != section_target_expected_count
        ):
            blockers.append("production_section_expected_count_mismatch")
        if len(set(candidate_server_ids)) != len(candidate_server_ids) or any(
            not value or not value.startswith("zinsp_")
            for value in candidate_server_ids
        ):
            blockers.append("production_section_legal_server_note_ids_required")
        if (
            bool(scope.get("pn68_in_scope"))
            or stats.get("pn68yptt_present") is True
            or "PN68YPTT" in candidate_zotero_keys
        ):
            blockers.append("production_section_pn68_forbidden")
        if not validator_valid:
            blockers.append("validator_failed")
        if not confirm_write:
            blockers.append("confirm_write_required")
        if confirmation_context != NOTE_CORRECTION_SAVE_CONTEXT:
            blockers.append("confirmation_context_invalid")
        if not human_audit_confirmed:
            blockers.append("human_audit_invalid")

        blockers = list(dict.fromkeys(blockers))
        return {
            "allowed": not blockers,
            "reason": blockers[0] if blockers else None,
            "mode": "production_review_save_section",
            "is_production_db": True,
            "section_env": PRODUCTION_REVIEW_SAVE_SECTION_ENV,
            "section_env_enabled": section_enabled,
            "section_target_env": PRODUCTION_REVIEW_SAVE_SECTION_TARGET_ENV,
            "section_target": section_target or None,
            "section_target_expected_count": section_target_expected_count,
            "section_allowed_scope_ids": list(PRODUCTION_REVIEW_SECTION_ALLOWED_SCOPES),
            "section_deferred_scope_ids": dict(PRODUCTION_REVIEW_SECTION_DEFERRED_SCOPES),
            "request_document_id": int(document_id),
            "request_chapter_id": int(chapter_id),
            "request_review_mode": review_mode,
            "request_scope_id": request_scope_id,
            "request_canary_subscope": bool(canary_subscope),
            "expected_count": expected_count,
            "actual_count": actual_count,
            "selected_count": len(selected),
            "selected_server_note_ids": selected,
            "validator_valid": bool(validator_valid),
            "confirm_write": bool(confirm_write),
            "confirmation_context_valid": confirmation_context == NOTE_CORRECTION_SAVE_CONTEXT,
            "human_audit_confirmed": bool(human_audit_confirmed),
            "current_blockers": blockers,
            "allowed_write_tables": list(PRODUCTION_REVIEW_CANARY_WRITE_TABLES),
        }

    if not resolved_readiness.get("review_schema_ready"):
        blockers.append("review_schema_missing")
    if review_mode != "canary_subscope":
        blockers.append("production_canary_review_mode_required")
    if not canary_subscope:
        blockers.append("production_canary_subscope_confirmation_required")
    if legacy_selected:
        blockers.append("production_canary_selected_note_ids_alias_forbidden")
    if len(selected) < 1 or len(selected) > 3:
        blockers.append("production_canary_selected_count_must_be_1_3")
    if len(set(selected)) != len(selected):
        blockers.append("production_canary_selected_server_note_ids_must_be_unique")
    if not bool(scope.get("canary_subscope") or scope.get("is_canary_subscope")):
        blockers.append("production_canary_package_scope_required")
    if scope.get("parent_review_mode") != "section_scoped" or not scope.get("parent_scope_id"):
        blockers.append("production_canary_parent_scope_required")
    if selected != package_selected:
        blockers.append("production_canary_selected_server_note_ids_mismatch")
    if (
        any(not value for value in candidate_server_ids)
        or len(candidate_server_ids) != len(selected)
        or candidate_server_ids != selected
    ):
        blockers.append("production_canary_legal_server_note_ids_required")
    if not validator_valid:
        blockers.append("validator_failed")
    if not confirm_write:
        blockers.append("confirm_write_required")
    if confirmation_context != NOTE_CORRECTION_SAVE_CONTEXT:
        blockers.append("confirmation_context_invalid")
    if not human_audit_confirmed:
        blockers.append("human_audit_invalid")

    blockers = list(dict.fromkeys(blockers))
    return {
        "allowed": not blockers,
        "reason": blockers[0] if blockers else None,
        "mode": "production_review_save_canary",
        "is_production_db": True,
        "canary_env": PRODUCTION_REVIEW_SAVE_CANARY_ENV,
        "canary_env_enabled": canary_enabled,
        "request_review_mode": review_mode,
        "request_canary_subscope": bool(canary_subscope),
        "selected_count": len(selected),
        "selected_server_note_ids": selected,
        "parent_review_mode": scope.get("parent_review_mode"),
        "parent_scope_id": scope.get("parent_scope_id"),
        "validator_valid": bool(validator_valid),
        "confirm_write": bool(confirm_write),
        "confirmation_context_valid": confirmation_context == NOTE_CORRECTION_SAVE_CONTEXT,
        "human_audit_confirmed": bool(human_audit_confirmed),
        "current_blockers": blockers,
        "allowed_write_tables": list(PRODUCTION_REVIEW_CANARY_WRITE_TABLES),
    }


def _build_note_correction_review_save_section84_pn68_request_gate(
    *,
    document_id: int,
    chapter_id: int,
    review_mode: str,
    canary_subscope: bool,
    package: Mapping[str, Any],
    selected_server_note_ids: list[str] | None,
    selected_note_ids: list[str] | None,
    confirm_write: bool,
    confirmation_context: str | None,
    validator_valid: bool,
    human_audit_confirmed: bool,
    validation: Mapping[str, Any] | None,
    readiness: Mapping[str, Any],
) -> dict[str, Any]:
    scope = package.get("scope") or {}
    validation_payload = validation or {}
    completeness = validation_payload.get("completeness") or {}
    stats = validation_payload.get("stats") or {}
    expected_count = _int_or_none(completeness.get("expected_count"))
    actual_count = _int_or_none(completeness.get("actual_count"))
    section_target = production_review_save_section_target()
    scope_id = str(
        package.get("scope_id")
        or scope.get("scope_id")
        or scope.get("section_id")
        or ""
    ).strip()
    request_scope_id = str(scope_id or "").strip()
    package_section_id = str(scope.get("section_id") or "").strip()
    selected = [
        str(value or "").strip()
        for value in (selected_server_note_ids or [])
        if str(value or "").strip()
    ]
    legacy_selected = [
        str(value or "").strip()
        for value in (selected_note_ids or [])
        if str(value or "").strip()
    ]
    candidates = list(package.get("correction_candidates") or [])
    candidate_server_ids = [
        str(candidate.get("server_note_id") or "").strip()
        for candidate in candidates
    ]
    candidate_zotero_keys = [
        str(candidate.get("zotero_annotation_key") or "").strip()
        for candidate in candidates
    ]
    pn68_candidates = [
        candidate
        for candidate in candidates
        if str(candidate.get("zotero_annotation_key") or "").strip()
        == PRODUCTION_REVIEW_SECTION84_PN68_ZOTERO_KEY
    ]
    normalized_items = list(validation_payload.get("normalized_preview") or [])
    pn68_review_items = [
        item
        for item in normalized_items
        if str(item.get("zotero_annotation_key") or "").strip()
        == PRODUCTION_REVIEW_SECTION84_PN68_ZOTERO_KEY
    ]
    blockers: list[str] = []

    if not readiness.get("review_schema_ready"):
        blockers.append("review_schema_missing")
    if int(document_id) != PRODUCTION_REVIEW_SECTION_DOCUMENT_ID:
        blockers.append("production_section84_pn68_document_id_required")
    if int(chapter_id) != PRODUCTION_REVIEW_SECTION_CHAPTER_ID:
        blockers.append("production_section84_pn68_chapter_id_required")
    if review_mode != "section_scoped":
        blockers.append("production_section84_pn68_review_mode_required")
    if canary_subscope:
        blockers.append("production_section84_pn68_canary_subscope_forbidden")
    if not section_target:
        blockers.append("production_section84_pn68_target_required")
    elif section_target != PRODUCTION_REVIEW_SECTION84_PN68_SCOPE_ID:
        blockers.append("production_section84_pn68_target_required")
    if request_scope_id != PRODUCTION_REVIEW_SECTION84_PN68_SCOPE_ID:
        blockers.append("production_section84_pn68_scope_required")
    if package_section_id and package_section_id != PRODUCTION_REVIEW_SECTION84_PN68_SCOPE_ID:
        blockers.append("production_section84_pn68_package_scope_required")
    if legacy_selected:
        blockers.append("production_section84_pn68_selected_note_ids_alias_forbidden")
    if selected and selected != candidate_server_ids:
        blockers.append("production_section84_pn68_selected_server_note_ids_mismatch")
    if (
        expected_count != PRODUCTION_REVIEW_SECTION84_PN68_EXPECTED_COUNT
        or actual_count != PRODUCTION_REVIEW_SECTION84_PN68_EXPECTED_COUNT
        or len(candidate_server_ids) != PRODUCTION_REVIEW_SECTION84_PN68_EXPECTED_COUNT
    ):
        blockers.append("production_section84_pn68_expected_count_mismatch")
    if len(set(candidate_server_ids)) != len(candidate_server_ids) or any(
        not value or not value.startswith("zinsp_")
        for value in candidate_server_ids
    ):
        blockers.append("production_section84_pn68_legal_server_note_ids_required")

    pn68_candidate_count = candidate_zotero_keys.count(PRODUCTION_REVIEW_SECTION84_PN68_ZOTERO_KEY)
    if pn68_candidate_count == 0:
        blockers.append("production_section84_pn68_missing")
    elif pn68_candidate_count > 1:
        blockers.append("production_section84_pn68_duplicated")
    else:
        pn68_candidate = pn68_candidates[0]
        if str(pn68_candidate.get("server_note_id") or "").strip() != PRODUCTION_REVIEW_SECTION84_PN68_SERVER_NOTE_ID:
            blockers.append("production_section84_pn68_server_note_id_required")
        candidate_warnings = {
            str(value)
            for value in (pn68_candidate.get("warnings") or [])
            if str(value).strip()
        }
        if str(pn68_candidate.get("evidence_alignment_status") or "") != "unmatched":
            blockers.append("production_section84_pn68_alignment_status_required")
        if str(pn68_candidate.get("anchor_method") or "") != "unmatched":
            blockers.append("production_section84_pn68_anchor_method_required")
        if not PRODUCTION_REVIEW_SECTION84_PN68_REQUIRED_WARNINGS.issubset(candidate_warnings):
            blockers.append("production_section84_pn68_source_warning_required")

    if len(pn68_review_items) == 0:
        blockers.append("production_section84_pn68_review_item_missing")
    elif len(pn68_review_items) > 1:
        blockers.append("production_section84_pn68_review_item_duplicated")
    else:
        pn68_review_item = pn68_review_items[0]
        status = str(pn68_review_item.get("correction_status") or "")
        issue_type = str(pn68_review_item.get("issue_type") or "")
        reviewer_warning = str(pn68_review_item.get("reviewer_warning") or "")
        warning_text = f"{issue_type} {reviewer_warning}".lower()
        if str(pn68_review_item.get("server_note_id") or "") != PRODUCTION_REVIEW_SECTION84_PN68_SERVER_NOTE_ID:
            blockers.append("production_section84_pn68_review_server_note_id_required")
        if status not in PRODUCTION_REVIEW_SECTION84_PN68_ALLOWED_STATUSES:
            blockers.append("production_section84_pn68_status_invalid")
        if not pn68_review_item.get("has_alignment_warning"):
            blockers.append("production_section84_pn68_review_warning_required")
        if "alignment" not in warning_text and "unmatched" not in warning_text:
            blockers.append("production_section84_pn68_warning_not_preserved")

    if stats.get("pn68yptt_present") is not True:
        blockers.append("production_section84_pn68_stats_required")
    if not bool(scope.get("pn68_in_scope")):
        blockers.append("production_section84_pn68_scope_flag_required")
    if not validator_valid:
        blockers.append("validator_failed")
    if not confirm_write:
        blockers.append("confirm_write_required")
    if confirmation_context != NOTE_CORRECTION_SAVE_CONTEXT:
        blockers.append("confirmation_context_invalid")
    if not human_audit_confirmed:
        blockers.append("human_audit_invalid")

    blockers = list(dict.fromkeys(blockers))
    return {
        "allowed": not blockers,
        "reason": blockers[0] if blockers else None,
        "mode": "production_review_save_section84_pn68",
        "is_production_db": True,
        "section84_pn68_env": PRODUCTION_REVIEW_SAVE_SECTION84_PN68_ENV,
        "section84_pn68_env_enabled": True,
        "section_target_env": PRODUCTION_REVIEW_SAVE_SECTION_TARGET_ENV,
        "section_target": section_target or None,
        "request_document_id": int(document_id),
        "request_chapter_id": int(chapter_id),
        "request_review_mode": review_mode,
        "request_scope_id": request_scope_id,
        "request_canary_subscope": bool(canary_subscope),
        "expected_count": expected_count,
        "actual_count": actual_count,
        "section84_pn68_expected_count": PRODUCTION_REVIEW_SECTION84_PN68_EXPECTED_COUNT,
        "pn68_candidate_count": pn68_candidate_count,
        "pn68_review_item_count": len(pn68_review_items),
        "pn68_server_note_id": PRODUCTION_REVIEW_SECTION84_PN68_SERVER_NOTE_ID,
        "pn68_zotero_annotation_key": PRODUCTION_REVIEW_SECTION84_PN68_ZOTERO_KEY,
        "pn68_warning_preserved": not any(
            blocker.startswith("production_section84_pn68")
            for blocker in blockers
            if blocker not in {
                "production_section84_pn68_target_required",
                "production_section84_pn68_scope_required",
                "production_section84_pn68_package_scope_required",
                "production_section84_pn68_expected_count_mismatch",
                "production_section84_pn68_legal_server_note_ids_required",
                "production_section84_pn68_selected_note_ids_alias_forbidden",
                "production_section84_pn68_selected_server_note_ids_mismatch",
                "production_section84_pn68_document_id_required",
                "production_section84_pn68_chapter_id_required",
                "production_section84_pn68_review_mode_required",
                "production_section84_pn68_canary_subscope_forbidden",
            }
        ),
        "selected_count": len(selected),
        "selected_server_note_ids": selected,
        "validator_valid": bool(validator_valid),
        "confirm_write": bool(confirm_write),
        "confirmation_context_valid": confirmation_context == NOTE_CORRECTION_SAVE_CONTEXT,
        "human_audit_confirmed": bool(human_audit_confirmed),
        "current_blockers": blockers,
        "allowed_write_tables": list(PRODUCTION_REVIEW_CANARY_WRITE_TABLES),
    }


def build_note_correction_review_production_canary_preflight(
    *,
    research_db_path: str | Path = DEFAULT_DB_PATH,
    document_id: int | None = None,
    chapter_id: int | None = None,
) -> dict[str, Any]:
    db_path = Path(research_db_path)
    readiness = build_note_correction_review_save_readiness(research_db_path=db_path)
    chapter_exists = None
    chapter_lookup_error = None
    if document_id is not None and chapter_id is not None:
        try:
            chapter_exists = _chapter_exists(
                db_path,
                document_id=int(document_id),
                chapter_id=int(chapter_id),
            )
        except sqlite3.Error as exc:
            chapter_lookup_error = str(exc)
            chapter_exists = False

    blockers = list(readiness.get("current_blockers") or [])
    if chapter_exists is False and "chapter_not_found" not in blockers:
        blockers.append("chapter_not_found")
    if chapter_lookup_error and "chapter_lookup_failed" not in blockers:
        blockers.append("chapter_lookup_failed")

    flags = review_pipeline_safety_flags()
    return {
        "status": "ok",
        "mode": "note_correction_review_production_canary_preflight",
        "document_id": document_id,
        "chapter_id": chapter_id,
        "db_path": str(db_path),
        "review_schema_ready": bool(readiness.get("review_schema_ready")),
        "save_endpoint_available": bool(readiness.get("save_endpoint_available")),
        "required_confirmation_context": NOTE_CORRECTION_SAVE_CONTEXT,
        "production_db_write_enabled": bool(readiness.get("production_db_write_enabled")),
        "production_review_write_allowed": bool(readiness.get("production_review_write_allowed")) and chapter_exists is not False,
        "current_blockers": list(dict.fromkeys(blockers)),
        "chapter_exists": chapter_exists,
        "chapter_lookup_error": chapter_lookup_error,
        "schema_version": NOTE_CORRECTION_REVIEW_SAVE_SCHEMA_VERSION,
        "human_audit_schema_version": NOTE_CORRECTION_HUMAN_AUDIT_SCHEMA_VERSION,
        "canary_preflight_only": True,
        "real_save_api_called": False,
        "safety_flags": flags,
        **flags,
    }


def _review_save_block_reason(readiness: Mapping[str, Any]) -> str:
    blockers = list(readiness.get("current_blockers") or [])
    if "production_db_write_disabled" in blockers:
        return "production_db_write_disabled"
    if "review_schema_missing" in blockers:
        return "review_schema_missing"
    if "review_db_unavailable" in blockers:
        return "review_db_unavailable"
    return "production_review_write_not_allowed"
