"""Chapter review normalization responsibilities."""

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

def _hash_json_for_contract(value: Mapping[str, Any]) -> str:
    payload = {
        key: child
        for key, child in value.items()
        if key != "copy_ready_prompt"
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def _merge_scope_complete(*, review_mode: str, merge_preview: Mapping[str, Any] | None) -> bool:
    if review_mode == "full_chapter":
        return True
    if not isinstance(merge_preview, Mapping):
        return False
    if merge_preview.get("all_valid") is True:
        return True
    expected_total = _int_or_none(merge_preview.get("expected_total"))
    validated_items = _int_or_none(merge_preview.get("validated_items"))
    missing = _int_or_none(merge_preview.get("missing"))
    if expected_total is not None and validated_items is not None:
        return expected_total > 0 and validated_items >= expected_total and (missing in (None, 0))
    return False


def _merge_preview_summary(merge_preview: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(merge_preview, Mapping):
        return None
    return {
        "expected_total": _int_or_none(merge_preview.get("expected_total")),
        "validated_items": _int_or_none(merge_preview.get("validated_items")),
        "missing": _int_or_none(merge_preview.get("missing")),
        "all_valid": bool(merge_preview.get("all_valid")) if "all_valid" in merge_preview else None,
    }


def _candidate_order_for_item(item: Mapping[str, Any], candidate_order: Mapping[str, int]) -> int:
    return candidate_order.get(_item_key(item), 10**9)


def _normalized_item_from_saved_row(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "server_note_id": item.get("server_note_id"),
        "client_note_id": item.get("client_note_id"),
        "zotero_annotation_key": item.get("zotero_annotation_key"),
        "correction_status": item.get("ai_correction_status"),
        "issue_type": item.get("ai_issue_type"),
        "explanation": item.get("ai_explanation"),
        "suggested_revision": item.get("ai_suggested_revision"),
        "evidence_support": item.get("ai_evidence_support"),
        "confidence": item.get("ai_confidence"),
        "reviewer_warning": item.get("ai_reviewer_warning"),
    }


def _ignored_review_item_summary(item: Mapping[str, Any], *, reason: str) -> dict[str, Any]:
    return {
        "reason": reason,
        "review_id": item.get("review_id"),
        "review_mode": item.get("source_review_mode") or item.get("review_mode"),
        "scope_id": item.get("source_scope_id") or item.get("scope_id"),
        "server_note_id": item.get("server_note_id"),
        "client_note_id": item.get("client_note_id"),
        "zotero_annotation_key": item.get("zotero_annotation_key"),
    }


def _saved_correction_review_merge_complete(saved: Mapping[str, Any]) -> bool:
    review_mode = str(saved.get("review_mode") or "full_chapter")
    merge_scope = saved.get("merge_scope") or saved.get("merge_scope_json")
    if isinstance(merge_scope, str):
        try:
            merge_scope = json.loads(merge_scope)
        except json.JSONDecodeError:
            merge_scope = None
    return _merge_scope_complete(review_mode=review_mode, merge_preview=merge_scope)


def _canary_subscope_response_metadata(
    package: Mapping[str, Any],
    validation: Mapping[str, Any],
) -> dict[str, Any]:
    scope = package.get("scope") or {}
    is_canary = bool(scope.get("canary_subscope") or scope.get("is_canary_subscope"))
    if not is_canary:
        return {}
    completeness = validation.get("completeness") or {}
    return {
        "canary_subscope": True,
        "is_canary_subscope": True,
        "parent_review_mode": scope.get("parent_review_mode"),
        "parent_scope_id": scope.get("parent_scope_id"),
        "parent_scope_expected_count": int(completeness.get("parent_scope_expected_count") or scope.get("parent_scope_expected_count") or 0),
        "original_scope_expected_count": int(
            completeness.get("original_scope_expected_count") or scope.get("original_scope_expected_count") or 0
        ),
        "canary_selected_count": int(completeness.get("canary_selected_count") or scope.get("canary_selected_count") or 0),
        "selected_server_note_ids": list(scope.get("selected_server_note_ids") or []),
        "source_package_hash": package.get("source_package_hash") or scope.get("source_package_hash"),
        "parent_package_hash": package.get("parent_package_hash") or scope.get("parent_package_hash"),
    }


def _primary_note_identity_keys(item: Mapping[str, Any] | None) -> list[str]:
    if not item:
        return []
    keys: list[str] = []
    for value in [item.get("server_note_id"), item.get("client_note_id")]:
        text = str(value or "").strip()
        if text and text not in keys:
            keys.append(text)
    return keys


def _item_key(item: Mapping[str, Any]) -> str:
    return str(
        item.get("server_note_id")
        or item.get("client_note_id")
        or item.get("note_id")
        or item.get("zotero_annotation_key")
        or ""
    ).strip()


def _canonical_review_json(review_payload: str | Mapping[str, Any]) -> str:
    if isinstance(review_payload, str):
        parsed = json.loads(review_payload)
    else:
        parsed = dict(review_payload)
    return json.dumps(parsed, ensure_ascii=False, sort_keys=True)


def _hash_review(*parts: str) -> str:
    import hashlib

    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def _parse_review_payload(value: str | Mapping[str, Any], errors: list[str]) -> dict[str, Any] | None:
    if isinstance(value, Mapping):
        return dict(value)
    if not isinstance(value, str) or not value.strip():
        errors.append("review JSON is required")
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        errors.append(f"JSON parse error: {exc.msg}")
        return None
    if not isinstance(parsed, dict):
        errors.append("review JSON root must be an object")
        return None
    return parsed


def _loads(value: Any, default: Any) -> Any:
    if value is None:
        return default
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def _str_or_none(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _is_confidence_score(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if not isinstance(value, (int, float)):
        return False
    return 0 <= float(value) <= 1


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()
