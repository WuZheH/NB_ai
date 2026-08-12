"""Chapter review validation responsibilities."""

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
    _is_confidence_score,
    _item_key,
    _primary_note_identity_keys,
    _str_or_none,
)

def _normalized_review_root(validation: Mapping[str, Any]) -> dict[str, Any]:
    normalized = validation.get("normalized_json") or {}
    if isinstance(normalized, Mapping) and isinstance(normalized.get("note_correction_review"), Mapping):
        return {"note_correction_review": dict(normalized.get("note_correction_review") or {})}
    if isinstance(normalized, Mapping):
        return {"note_correction_review": dict(normalized)}
    return {"note_correction_review": {}}


def _normalize_human_audit_items(
    *,
    validation: Mapping[str, Any],
    package: Mapping[str, Any],
    human_audit_items: list[Mapping[str, Any]],
) -> dict[str, Any]:
    errors: list[str] = []
    candidates = list(package.get("correction_candidates") or [])
    originals = _candidate_original_index(candidates)
    ai_items = list(validation.get("normalized_preview") or [])
    audits = _human_audit_index(human_audit_items, errors)
    normalized_items: list[dict[str, Any]] = []

    for index, ai_item in enumerate(ai_items):
        key = _item_key(ai_item)
        if not key:
            errors.append(f"items[{index}] missing server_note_id/client_note_id")
            continue
        audit = audits.get(key)
        if not audit:
            errors.append(f"human_audit_items missing expected note {key}")
            continue
        original = originals.get(key)
        if not original:
            errors.append(f"human_audit_items[{key}] does not match original correction candidate")
            continue
        normalized = _normalize_one_human_audit_item(
            ai_item=ai_item,
            audit=audit,
            original=original,
            key=key,
            errors=errors,
        )
        if normalized:
            normalized_items.append(normalized)

    expected_audit_keys = {
        key
        for item in ai_items
        for key in _primary_note_identity_keys(item)
    }
    unexpected = sorted(set(audits) - expected_audit_keys)
    for key in unexpected[:10]:
        errors.append(f"human_audit_items unexpected note {key}")
    return {
        "items": normalized_items,
        "errors": errors,
        "confirmed_count": sum(1 for item in normalized_items if item.get("confirmed_by_user")),
        "needs_followup_count": sum(1 for item in normalized_items if item.get("human_action") == "needs_followup"),
        "final_note_text_count": sum(1 for item in normalized_items if _str_or_none(item.get("final_note_text"))),
    }


def _candidate_original_index(candidates: list[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    index: dict[str, Mapping[str, Any]] = {}
    for candidate in candidates:
        for key in _primary_note_identity_keys(candidate):
            index.setdefault(key, candidate)
    return index


def _human_audit_index(items: list[Mapping[str, Any]], errors: list[str]) -> dict[str, Mapping[str, Any]]:
    index: dict[str, Mapping[str, Any]] = {}
    for position, item in enumerate(items):
        if not isinstance(item, Mapping):
            errors.append(f"human_audit_items[{position}] must be an object")
            continue
        keys = _primary_note_identity_keys(item)
        if not keys:
            if item.get("zotero_annotation_key"):
                errors.append(f"human_audit_items[{position}] cannot use zotero_annotation_key as primary identity")
            else:
                errors.append(f"human_audit_items[{position}] must include server_note_id or client_note_id")
            continue
        for key in keys:
            if key in index:
                errors.append(f"human_audit_items duplicate note identity {key}")
            else:
                index[key] = item
    return index


def _normalize_one_human_audit_item(
    *,
    ai_item: Mapping[str, Any],
    audit: Mapping[str, Any],
    original: Mapping[str, Any],
    key: str,
    errors: list[str],
) -> dict[str, Any] | None:
    server_note_id = _str_or_none(ai_item.get("server_note_id")) or _str_or_none(original.get("server_note_id"))
    client_note_id = _str_or_none(ai_item.get("client_note_id")) or _str_or_none(original.get("client_note_id"))
    if not server_note_id:
        errors.append(f"human_audit_items[{key}] missing server_note_id")
        return None
    action = _str_or_none(audit.get("human_action") or audit.get("action")) or "pending"
    if action not in {"pending", "keep_original", "ai_revision_accepted", "manually_edited", "needs_followup"}:
        errors.append(f"human_audit_items[{key}].human_action is invalid")
        return None
    if action == "pending":
        errors.append(f"human_audit_items[{key}].human_action must be resolved before save")
    original_note_text = str(original.get("note_text") or "")
    final_note_text = _str_or_none(audit.get("final_note_text"))
    if action == "keep_original" and final_note_text is None:
        final_note_text = original_note_text
    if action in {"ai_revision_accepted", "manually_edited"} and not final_note_text:
        errors.append(f"human_audit_items[{key}].final_note_text is required for {action}")
    confirmed = bool(audit.get("confirmed_by_user") if "confirmed_by_user" in audit else audit.get("confirmed"))
    if not confirmed:
        errors.append(f"human_audit_items[{key}].confirmed_by_user is required")
    writeback_intent = _str_or_none(audit.get("writeback_intent")) or (
        "planned" if action in {"ai_revision_accepted", "manually_edited"} else "none"
    )
    if writeback_intent not in {"none", "planned"}:
        errors.append(f"human_audit_items[{key}].writeback_intent is invalid")
    writeback_target = _str_or_none(audit.get("writeback_target"))
    if writeback_intent == "planned":
        writeback_target = writeback_target or "zotero_annotation_comment"
    return {
        "server_note_id": server_note_id,
        "client_note_id": client_note_id,
        "zotero_annotation_key": _str_or_none(ai_item.get("zotero_annotation_key")) or _str_or_none(original.get("zotero_annotation_key")),
        "page": _int_or_none(ai_item.get("page") if ai_item.get("page") is not None else original.get("page")),
        "original_note_text": original_note_text,
        "selected_text": original.get("selected_text") or "",
        "ai_correction_status": str(ai_item.get("correction_status") or ""),
        "ai_issue_type": str(ai_item.get("issue_type") or ""),
        "ai_explanation": str(ai_item.get("explanation") or ""),
        "ai_suggested_revision": ai_item.get("suggested_revision"),
        "ai_evidence_support": str(ai_item.get("evidence_support") or ""),
        "ai_confidence": float(ai_item["confidence"]) if _is_confidence_score(ai_item.get("confidence")) else None,
        "ai_reviewer_warning": ai_item.get("reviewer_warning"),
        "human_action": action,
        "final_note_text": final_note_text,
        "confirmed_by_user": confirmed,
        "writeback_intent": writeback_intent,
        "writeback_status": "not_started",
        "writeback_target": writeback_target,
    }


from app.services.chapter_note_correction_prompt_service import (
    normalize_chatgpt_note_correction_review,
    validate_chapter_note_correction_batch_review,
    validate_chapter_note_correction_section_review,
)


_VALIDATOR_EXPORTS = {
    "validate_chapter_note_classification_manual_json": "classification_validation",
    "validate_chapter_note_classification_review": "classification_validation",
    "validate_object_candidate_draft_save_payload": "object_draft_review",
    "validate_object_candidate_human_review_payload": "object_human_review",
    "validate_phase7d_object_candidate_dry_run_candidates": "object_candidates",
    "validate_relation_candidate_dry_run_package": "relations",
}


def __getattr__(name: str) -> Any:
    module_name = _VALIDATOR_EXPORTS.get(name)
    if module_name:
        from importlib import import_module

        module = import_module(f"app.domains.chapter_review.{module_name}")
        return getattr(module, name)
    raise AttributeError(name)
