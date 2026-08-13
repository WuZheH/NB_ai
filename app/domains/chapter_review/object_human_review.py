"""Chapter review object human review responsibilities."""

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

from .loading import (
    load_saved_object_candidate_draft_review,
    load_saved_object_candidate_human_review,
)

from .normalization import (
    _hash_json_for_contract,
    _is_confidence_score,
    _loads,
    _utc_now,
)

from .object_review_common import (
    _object_candidate_payload_requests_forbidden_side_effects,
    _truthy_true,
)

from .persistence import (
    _connect_rw_existing,
    _latest_object_candidate_human_review_row,
)

from .safety import (
    review_pipeline_safety_flags,
)

from .schema import (
    build_object_candidate_human_review_schema_audit,
    ensure_object_candidate_human_review_tables,
)

from .write_policy import (
    is_production_object_candidate_human_review_save_enabled,
)

def build_object_candidate_human_review_workbench(
    *,
    research_db_path: str | Path = DEFAULT_DB_PATH,
    document_id: int,
    chapter_id: int,
) -> dict[str, Any]:
    db_path = Path(research_db_path)
    draft = load_saved_object_candidate_draft_review(
        research_db_path=db_path,
        document_id=document_id,
        chapter_id=chapter_id,
        source_classification_review_id=PRODUCTION_OBJECT_CANDIDATE_DRAFT_SOURCE_REVIEW_ID,
    )
    flags = review_pipeline_safety_flags()
    if not draft:
        return {
            "status": "blocked",
            "mode": "r3_phase7f_object_candidate_human_review_workbench",
            "document_id": document_id,
            "chapter_id": chapter_id,
            "reason": "object_candidate_draft_review_not_saved",
            "review_id": None,
            "candidate_count": 0,
            "pending_count": 0,
            "candidates": [],
            "pn68_quarantined": False,
            "relation_generation_locked": True,
            "mechanism_generation_locked": True,
            "safety_flags": flags,
            **flags,
        }
    saved_human_review = load_saved_object_candidate_human_review(
        research_db_path=db_path,
        document_id=document_id,
        chapter_id=chapter_id,
        source_draft_review_id=str(draft.get("review_id") or ""),
    )
    human_items_by_candidate = {
        str(item.get("candidate_temp_id") or ""): item
        for item in ((saved_human_review or {}).get("items") or [])
        if str(item.get("candidate_temp_id") or "")
    }
    candidates = [
        _phase7f_workbench_candidate(item, human_items_by_candidate.get(str(item.get("candidate_temp_id") or "")))
        for item in (draft.get("items") or [])
    ]
    counts = Counter(str(candidate.get("current_human_action") or "pending") for candidate in candidates)
    return {
        "status": "human_review_saved" if saved_human_review else "ready",
        "mode": "r3_phase7f_object_candidate_human_review_workbench",
        "document_id": document_id,
        "chapter_id": chapter_id,
        "review_id": draft.get("review_id"),
        "object_candidate_draft_review_id": draft.get("review_id"),
        "source_classification_review_id": draft.get("source_classification_review_id"),
        "human_review_id": (saved_human_review or {}).get("human_review_id"),
        "candidate_count": len(candidates),
        "approved_count": int(counts.get("approve", 0) + counts.get("edit", 0)),
        "rejected_count": int(counts.get("reject", 0)),
        "merged_count": int(counts.get("merge", 0)),
        "pending_count": int(counts.get("pending", 0)),
        "pn68_quarantined": bool(draft.get("pn68_quarantined")),
        "pn68_source_candidate_count": sum(
            1
            for candidate in candidates
            if PRODUCTION_REVIEW_SECTION84_PN68_SERVER_NOTE_ID in (candidate.get("source_server_note_ids") or [])
        ),
        "relation_generation_locked": True,
        "mechanism_generation_locked": True,
        "ready_for_relation_dry_run": bool(saved_human_review and int((saved_human_review or {}).get("approved_count") or 0) > 0),
        "candidates": candidates,
        "saved_human_review": saved_human_review,
        "safety_flags": flags,
        **flags,
    }


def build_phase7f_object_candidate_human_review_fixture(
    *,
    research_db_path: str | Path = DEFAULT_DB_PATH,
    document_id: int,
    chapter_id: int,
) -> dict[str, Any]:
    workbench = build_object_candidate_human_review_workbench(
        research_db_path=research_db_path,
        document_id=document_id,
        chapter_id=chapter_id,
    )
    if workbench.get("status") == "blocked":
        return {
            "status": "blocked",
            "reason": workbench.get("reason"),
            "document_id": document_id,
            "chapter_id": chapter_id,
            "items": [],
            **review_pipeline_safety_flags(),
        }
    items = []
    for candidate in workbench.get("candidates") or []:
        object_type = str(candidate.get("object_type") or "")
        confidence = candidate.get("confidence")
        clean_known_object = object_type in {
            "concept",
            "method",
            "algorithm",
            "model",
            "metric",
            "dataset",
            "theorem_or_principle",
        } and _is_confidence_score(confidence) and float(confidence) >= 0.58
        low_confidence_research_problem = object_type == "research_problem" and _is_confidence_score(confidence) and float(confidence) <= 0.52
        if clean_known_object:
            action = "approve"
            note = "Conservative fixture: explicit known object term with supported source notes."
        elif low_confidence_research_problem:
            action = "reject"
            note = "Conservative fixture: low-confidence note-derived research problem, leave out of approved object candidates."
        else:
            action = "pending"
            note = "Conservative fixture: requires later human decision before relation or mechanism prep."
        items.append(
            {
                "candidate_id": candidate.get("candidate_id"),
                "candidate_temp_id": candidate.get("candidate_temp_id"),
                "action": action,
                "object_name": candidate.get("object_name"),
                "object_type": candidate.get("object_type"),
                "merge_target_candidate_id": None,
                "human_note": note,
                "relation_generated": False,
                "mechanism_generated": False,
                "zotero_write_performed": False,
                "vector_write_performed": False,
            }
        )
    stats = Counter(str(item.get("action") or "pending") for item in items)
    flags = review_pipeline_safety_flags()
    return {
        "status": "ready",
        "mode": "r3_phase7f_object_candidate_human_review_fixture",
        "schema_version": OBJECT_CANDIDATE_HUMAN_REVIEW_SCHEMA_VERSION,
        "document_id": document_id,
        "chapter_id": chapter_id,
        "object_candidate_draft_review_id": workbench.get("object_candidate_draft_review_id"),
        "source_classification_review_id": workbench.get("source_classification_review_id"),
        "candidate_count": len(items),
        "approved_count": int(stats.get("approve", 0) + stats.get("edit", 0)),
        "rejected_count": int(stats.get("reject", 0)),
        "merged_count": int(stats.get("merge", 0)),
        "pending_count": int(stats.get("pending", 0)),
        "pn68_quarantined": bool(workbench.get("pn68_quarantined")),
        "items": items,
        "relation_generated": False,
        "mechanism_generated": False,
        "zotero_write_performed": False,
        "vector_write_performed": False,
        "object_candidates_generated": False,
        "approved_objects_created": False,
        "safety_flags": flags,
        **flags,
    }


def validate_object_candidate_human_review_payload(
    *,
    research_db_path: str | Path = DEFAULT_DB_PATH,
    document_id: int,
    chapter_id: int,
    review_payload: Mapping[str, Any] | list[Any] | None,
) -> dict[str, Any]:
    db_path = Path(research_db_path)
    workbench = build_object_candidate_human_review_workbench(
        research_db_path=db_path,
        document_id=document_id,
        chapter_id=chapter_id,
    )
    payload = _coerce_object_candidate_human_review_payload(review_payload)
    items = list(payload.get("items") or [])
    candidates = {
        str(candidate.get("candidate_id") or candidate.get("candidate_temp_id") or ""): candidate
        for candidate in (workbench.get("candidates") or [])
        if str(candidate.get("candidate_id") or candidate.get("candidate_temp_id") or "")
    }
    errors: list[str] = []
    invalid_items: list[dict[str, Any]] = []
    normalized_items: list[dict[str, Any]] = []
    seen: set[str] = set()
    allowed_actions = {"approve", "reject", "edit", "merge", "pending"}

    if workbench.get("status") == "blocked":
        errors.append(str(workbench.get("reason") or "object_candidate_workbench_not_ready"))
    if int(document_id) != PRODUCTION_OBJECT_CANDIDATE_DRAFT_DOCUMENT_ID:
        errors.append("production_object_candidate_human_review_document_id_required")
    if int(chapter_id) != PRODUCTION_OBJECT_CANDIDATE_DRAFT_CHAPTER_ID:
        errors.append("production_object_candidate_human_review_chapter_id_required")
    if payload.get("object_candidate_draft_review_id") != PRODUCTION_OBJECT_CANDIDATE_DRAFT_REVIEW_ID:
        errors.append("object_candidate_draft_review_id_mismatch")
    if workbench.get("object_candidate_draft_review_id") != PRODUCTION_OBJECT_CANDIDATE_DRAFT_REVIEW_ID:
        errors.append("saved_object_candidate_draft_review_id_mismatch")
    if _object_candidate_payload_requests_forbidden_side_effects(payload):
        errors.append("object_candidate_human_review_payload_side_effect_fields_forbidden")
    if len(items) != PRODUCTION_OBJECT_CANDIDATE_DRAFT_EXPECTED_COUNT:
        errors.append("candidate_review_item_count_mismatch")
    if workbench.get("pn68_quarantined") is not True:
        errors.append("pn68_quarantined_required")

    for index, item in enumerate(items):
        if not isinstance(item, Mapping):
            invalid_items.append({"index": index, "candidate_id": None, "errors": ["item must be an object"]})
            continue
        item_errors: list[str] = []
        candidate_id = str(item.get("candidate_id") or item.get("candidate_temp_id") or "").strip()
        candidate = candidates.get(candidate_id)
        action = str(item.get("action") or item.get("human_action") or "pending").strip()
        object_name = str(item.get("object_name") or item.get("final_object_name") or (candidate or {}).get("object_name") or "").strip()
        object_type = str(item.get("object_type") or item.get("final_object_type") or (candidate or {}).get("object_type") or "").strip()
        merge_target = str(item.get("merge_target_candidate_id") or item.get("merge_target_candidate_temp_id") or "").strip() or None
        if not candidate_id:
            item_errors.append("candidate_id required")
        elif candidate_id in seen:
            item_errors.append("duplicate candidate assignment")
        else:
            seen.add(candidate_id)
        if not candidate:
            item_errors.append("unknown candidate_id")
        if action not in allowed_actions:
            item_errors.append("action invalid")
        if action in {"approve", "edit"}:
            if not object_name:
                item_errors.append("approved object_name required")
            if object_type not in OBJECT_CANDIDATE_DRY_RUN_TYPES:
                item_errors.append("approved object_type invalid")
        if action == "edit":
            original_name = str((candidate or {}).get("object_name") or "")
            original_type = str((candidate or {}).get("object_type") or "")
            if object_name == original_name and object_type == original_type:
                item_errors.append("edit action requires changed object_name or object_type")
        if action == "merge":
            if not merge_target:
                item_errors.append("merge_target_candidate_id required")
            elif merge_target not in candidates:
                item_errors.append("merge target does not exist")
            elif merge_target == candidate_id:
                item_errors.append("merge target cannot be self")
        if candidate and PRODUCTION_REVIEW_SECTION84_PN68_SERVER_NOTE_ID in (candidate.get("source_server_note_ids") or []):
            item_errors.append("PN68 source candidate forbidden")
        if _truthy_true(item.get("relation_generated")):
            item_errors.append("relation_generated must be false")
        if _truthy_true(item.get("mechanism_generated")):
            item_errors.append("mechanism_generated must be false")
        if _truthy_true(item.get("zotero_write_performed")) or _truthy_true(item.get("zotero_db_write_performed")):
            item_errors.append("zotero_write_performed must be false")
        if _truthy_true(item.get("vector_write_performed")) or _truthy_true(item.get("vector_store_write_performed")):
            item_errors.append("vector_write_performed must be false")
        if item_errors:
            invalid_items.append({"index": index, "candidate_id": candidate_id, "errors": item_errors})
            errors.extend(item_errors)
            continue
        normalized_items.append(
            {
                "candidate_id": candidate_id,
                "candidate_temp_id": candidate_id,
                "source_draft_item_id": candidate.get("review_item_id"),
                "action": action,
                "object_name": object_name,
                "object_type": object_type,
                "merge_target_candidate_id": merge_target,
                "merge_group_key": item.get("merge_group_key") or (candidate.get("duplicate_group_key") if action == "merge" else None),
                "human_note": str(item.get("human_note") or "").strip(),
                "approved_candidate": action in {"approve", "edit"},
                "source_server_note_ids": candidate.get("source_server_note_ids") or [],
                "source_labels": candidate.get("source_labels") or [],
                "evidence_chunk_ids": candidate.get("evidence_chunk_ids") or [],
                "page_labels": candidate.get("page_labels") or [],
                "duplicate_group_key": candidate.get("duplicate_group_key"),
            }
        )

    missing_ids = sorted(set(candidates) - seen)
    unexpected_ids = sorted(seen - set(candidates))
    if missing_ids:
        errors.append("missing candidate assignments")
    if unexpected_ids:
        errors.append("unexpected candidate assignments")
    stats = Counter(str(item.get("action") or "pending") for item in normalized_items)
    review_payload_hash = _hash_json_for_contract(
        {
            "schema_version": OBJECT_CANDIDATE_HUMAN_REVIEW_SCHEMA_VERSION,
            "document_id": document_id,
            "chapter_id": chapter_id,
            "object_candidate_draft_review_id": payload.get("object_candidate_draft_review_id"),
            "items": normalized_items,
        }
    )
    flags = review_pipeline_safety_flags()
    return {
        "valid": not errors and not invalid_items,
        "mode": "r3_phase7f_object_candidate_human_review_validate",
        "schema_version": OBJECT_CANDIDATE_HUMAN_REVIEW_SCHEMA_VERSION,
        "document_id": document_id,
        "chapter_id": chapter_id,
        "object_candidate_draft_review_id": payload.get("object_candidate_draft_review_id"),
        "source_classification_review_id": workbench.get("source_classification_review_id"),
        "candidate_count": len(normalized_items),
        "expected_candidate_count": PRODUCTION_OBJECT_CANDIDATE_DRAFT_EXPECTED_COUNT,
        "approved_count": int(stats.get("approve", 0) + stats.get("edit", 0)),
        "rejected_count": int(stats.get("reject", 0)),
        "edited_count": int(stats.get("edit", 0)),
        "merged_count": int(stats.get("merge", 0)),
        "pending_count": int(stats.get("pending", 0)),
        "missing_candidate_ids": missing_ids,
        "unexpected_candidate_ids": unexpected_ids,
        "invalid_item_count": len(invalid_items),
        "errors": sorted(set(errors)),
        "invalid_items": invalid_items,
        "normalized_items": normalized_items,
        "review_payload_hash": review_payload_hash,
        "pn68_quarantined": bool(workbench.get("pn68_quarantined")),
        "pn68_source_candidate_count": int(workbench.get("pn68_source_candidate_count") or 0),
        "relation_generated": False,
        "mechanism_generated": False,
        "zotero_write_performed": False,
        "vector_write_performed": False,
        "object_candidates_generated": False,
        "approved_objects_created": False,
        "safety_flags": flags,
        **flags,
    }


def build_object_candidate_human_review_save_readiness(
    *,
    research_db_path: str | Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    schema_audit = build_object_candidate_human_review_schema_audit(research_db_path=research_db_path)
    blockers: list[str] = []
    if not is_production_object_candidate_human_review_save_enabled():
        blockers.append("production_object_candidate_human_review_save_disabled")
    flags = review_pipeline_safety_flags()
    return {
        "status": "ok",
        "mode": "r3_phase7f_object_candidate_human_review_save_readiness",
        "db_path": str(Path(research_db_path)),
        "schema_ready": bool(schema_audit.get("schema_ready")),
        "draft_schema_ready": bool(schema_audit.get("draft_schema_ready")),
        "schema_version": OBJECT_CANDIDATE_HUMAN_REVIEW_SCHEMA_VERSION,
        "production_object_candidate_human_review_save_env": PRODUCTION_OBJECT_CANDIDATE_HUMAN_REVIEW_SAVE_ENV,
        "production_object_candidate_human_review_save_enabled": is_production_object_candidate_human_review_save_enabled(),
        "production_object_candidate_human_review_write_allowed": not blockers,
        "write_available": not blockers,
        "required_confirmation_context": OBJECT_CANDIDATE_HUMAN_REVIEW_SAVE_CONTEXT,
        "allowed_document_id": PRODUCTION_OBJECT_CANDIDATE_DRAFT_DOCUMENT_ID,
        "allowed_chapter_id": PRODUCTION_OBJECT_CANDIDATE_DRAFT_CHAPTER_ID,
        "required_draft_review_id": PRODUCTION_OBJECT_CANDIDATE_DRAFT_REVIEW_ID,
        "allowed_write_tables": list(PRODUCTION_OBJECT_CANDIDATE_HUMAN_REVIEW_WRITE_TABLES),
        "current_blockers": blockers,
        "schema_audit": schema_audit,
        "safety_flags": flags,
        **flags,
    }


def build_object_candidate_human_review_save_request_gate(
    *,
    research_db_path: str | Path,
    document_id: int,
    chapter_id: int,
    validation: Mapping[str, Any],
    confirm_write: bool,
    confirmation_context: str | None,
    readiness: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    db_path = Path(research_db_path)
    resolved_readiness = dict(
        readiness
        or build_object_candidate_human_review_save_readiness(research_db_path=db_path)
    )
    blockers: list[str] = list(resolved_readiness.get("current_blockers") or [])
    if int(document_id) != PRODUCTION_OBJECT_CANDIDATE_DRAFT_DOCUMENT_ID:
        blockers.append("production_object_candidate_human_review_document_id_required")
    if int(chapter_id) != PRODUCTION_OBJECT_CANDIDATE_DRAFT_CHAPTER_ID:
        blockers.append("production_object_candidate_human_review_chapter_id_required")
    if validation.get("valid") is not True:
        blockers.append("object_candidate_human_review_validation_failed")
    if validation.get("object_candidate_draft_review_id") != PRODUCTION_OBJECT_CANDIDATE_DRAFT_REVIEW_ID:
        blockers.append("object_candidate_draft_review_id_mismatch")
    if int(validation.get("candidate_count") or 0) != PRODUCTION_OBJECT_CANDIDATE_DRAFT_EXPECTED_COUNT:
        blockers.append("candidate_count_mismatch")
    if not confirm_write:
        blockers.append("confirm_write_required")
    if confirmation_context != OBJECT_CANDIDATE_HUMAN_REVIEW_SAVE_CONTEXT:
        blockers.append("confirmation_context_invalid")
    if any(
        bool(validation.get(key))
        for key in [
            "llm_called",
            "zotero_write_performed",
            "vector_write_performed",
            "object_candidates_generated",
            "approved_objects_created",
            "relation_generated",
            "mechanism_generated",
            "generation_performed",
        ]
    ):
        blockers.append("object_candidate_human_review_side_effect_flags_forbidden")
    existing = load_saved_object_candidate_human_review(
        research_db_path=db_path,
        document_id=document_id,
        chapter_id=chapter_id,
        source_draft_review_id=PRODUCTION_OBJECT_CANDIDATE_DRAFT_REVIEW_ID,
    )
    if existing:
        blockers.append("object_candidate_human_review_already_saved")
    blockers = list(dict.fromkeys(blockers))
    return {
        "allowed": not blockers,
        "reason": blockers[0] if blockers else None,
        "mode": "production_object_candidate_human_review_save",
        "db_path": str(db_path),
        "document_id": document_id,
        "chapter_id": chapter_id,
        "human_review_env": PRODUCTION_OBJECT_CANDIDATE_HUMAN_REVIEW_SAVE_ENV,
        "human_review_env_enabled": is_production_object_candidate_human_review_save_enabled(),
        "required_confirmation_context": OBJECT_CANDIDATE_HUMAN_REVIEW_SAVE_CONTEXT,
        "request_confirm_write": bool(confirm_write),
        "request_confirmation_context": confirmation_context,
        "object_candidate_draft_review_id": validation.get("object_candidate_draft_review_id"),
        "expected_candidate_count": PRODUCTION_OBJECT_CANDIDATE_DRAFT_EXPECTED_COUNT,
        "actual_candidate_count": int(validation.get("candidate_count") or 0),
        "existing_human_review_id": existing.get("human_review_id") if existing else None,
        "allowed_write_tables": list(PRODUCTION_OBJECT_CANDIDATE_HUMAN_REVIEW_WRITE_TABLES),
        "current_blockers": blockers,
    }


def save_object_candidate_human_review(
    *,
    research_db_path: str | Path = DEFAULT_DB_PATH,
    document_id: int,
    chapter_id: int,
    review_payload: Mapping[str, Any] | list[Any] | None,
    confirm_write: bool,
    confirmation_context: str | None,
) -> dict[str, Any]:
    db_path = Path(research_db_path)
    validation = validate_object_candidate_human_review_payload(
        research_db_path=db_path,
        document_id=document_id,
        chapter_id=chapter_id,
        review_payload=review_payload,
    )
    readiness = build_object_candidate_human_review_save_readiness(research_db_path=db_path)
    request_gate = build_object_candidate_human_review_save_request_gate(
        research_db_path=db_path,
        document_id=document_id,
        chapter_id=chapter_id,
        validation=validation,
        confirm_write=confirm_write,
        confirmation_context=confirmation_context,
        readiness=readiness,
    )
    if not request_gate["allowed"]:
        return _blocked_object_candidate_human_review_save_response(
            document_id=document_id,
            chapter_id=chapter_id,
            reason=str(request_gate.get("reason") or "object_candidate_human_review_save_blocked"),
            validation=validation,
            readiness=readiness,
            request_gate=request_gate,
        )
    schema_result = ensure_object_candidate_human_review_tables(research_db_path=db_path, execute=True)
    if not schema_result.get("schema_ready"):
        return _blocked_object_candidate_human_review_save_response(
            document_id=document_id,
            chapter_id=chapter_id,
            reason="object_candidate_human_review_schema_missing",
            validation=validation,
            readiness=readiness,
            request_gate=request_gate,
        )
    now = _utc_now()
    human_review_id = f"ochr_{uuid4().hex}"
    safety_flags = review_pipeline_safety_flags(
        db_write_performed=True,
        schema_version=OBJECT_CANDIDATE_HUMAN_REVIEW_SCHEMA_VERSION,
        object_candidate_human_review_saved=True,
        object_candidates_generated=False,
        approved_objects_created=False,
        relation_generated=False,
        mechanism_generated=False,
    )
    normalized_items = list(validation.get("normalized_items") or [])
    conn = _connect_rw_existing(db_path)
    try:
        conn.row_factory = sqlite3.Row
        if _latest_object_candidate_human_review_row(
            conn,
            document_id=document_id,
            chapter_id=chapter_id,
            source_draft_review_id=PRODUCTION_OBJECT_CANDIDATE_DRAFT_REVIEW_ID,
        ):
            return _blocked_object_candidate_human_review_save_response(
                document_id=document_id,
                chapter_id=chapter_id,
                reason="object_candidate_human_review_already_saved",
                validation=validation,
                readiness=readiness,
                request_gate={**request_gate, "allowed": False, "current_blockers": ["object_candidate_human_review_already_saved"]},
            )
        conn.execute(
            f"""
            INSERT INTO {OBJECT_CANDIDATE_HUMAN_REVIEW_TABLE} (
                human_review_id, document_id, chapter_id, source_draft_review_id,
                source_classification_review_id, review_mode, review_payload_hash,
                candidate_count, approved_count, rejected_count, edited_count,
                merged_count, pending_count, pn68_quarantined, review_status,
                confirmation_context, safety_flags_json, llm_called,
                db_write_performed, zotero_write_performed, vector_write_performed,
                object_candidates_generated, approved_objects_created,
                relation_generated, mechanism_generated, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                human_review_id,
                document_id,
                chapter_id,
                PRODUCTION_OBJECT_CANDIDATE_DRAFT_REVIEW_ID,
                PRODUCTION_OBJECT_CANDIDATE_DRAFT_SOURCE_REVIEW_ID,
                "manual_object_candidate_review",
                str(validation.get("review_payload_hash") or ""),
                int(validation.get("candidate_count") or 0),
                int(validation.get("approved_count") or 0),
                int(validation.get("rejected_count") or 0),
                int(validation.get("edited_count") or 0),
                int(validation.get("merged_count") or 0),
                int(validation.get("pending_count") or 0),
                1 if validation.get("pn68_quarantined") else 0,
                "saved",
                confirmation_context,
                json.dumps(safety_flags, ensure_ascii=False, sort_keys=True, default=str),
                0,
                1,
                0,
                0,
                0,
                0,
                0,
                0,
                now,
                now,
            ),
        )
        for item in normalized_items:
            conn.execute(
                f"""
                INSERT INTO {OBJECT_CANDIDATE_HUMAN_REVIEW_ITEM_TABLE} (
                    review_item_id, human_review_id, source_draft_review_id,
                    source_draft_item_id, candidate_temp_id, document_id,
                    chapter_id, action, final_object_name, final_object_type,
                    merge_target_candidate_temp_id, merge_group_key, human_note,
                    approved_candidate, source_server_note_ids_json,
                    source_labels_json, evidence_chunk_ids_json, page_labels_json,
                    duplicate_group_key, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"ochri_{uuid4().hex}",
                    human_review_id,
                    PRODUCTION_OBJECT_CANDIDATE_DRAFT_REVIEW_ID,
                    str(item.get("source_draft_item_id") or ""),
                    str(item.get("candidate_temp_id") or ""),
                    document_id,
                    chapter_id,
                    str(item.get("action") or "pending"),
                    item.get("object_name"),
                    item.get("object_type"),
                    item.get("merge_target_candidate_id"),
                    item.get("merge_group_key"),
                    item.get("human_note"),
                    1 if item.get("approved_candidate") else 0,
                    json.dumps(item.get("source_server_note_ids") or [], ensure_ascii=False, sort_keys=True, default=str),
                    json.dumps(item.get("source_labels") or [], ensure_ascii=False, sort_keys=True, default=str),
                    json.dumps(item.get("evidence_chunk_ids") or [], ensure_ascii=False, sort_keys=True, default=str),
                    json.dumps(item.get("page_labels") or [], ensure_ascii=False, sort_keys=True, default=str),
                    str(item.get("duplicate_group_key") or ""),
                    now,
                ),
            )
        conn.commit()
    finally:
        conn.close()
    saved = load_saved_object_candidate_human_review(
        research_db_path=db_path,
        document_id=document_id,
        chapter_id=chapter_id,
        source_draft_review_id=PRODUCTION_OBJECT_CANDIDATE_DRAFT_REVIEW_ID,
    )
    return {
        "status": "saved",
        "mode": "r3_phase7f_object_candidate_human_review_save",
        "schema_version": OBJECT_CANDIDATE_HUMAN_REVIEW_SCHEMA_VERSION,
        "document_id": document_id,
        "chapter_id": chapter_id,
        "object_candidate_human_review_id": human_review_id,
        "human_review_id": human_review_id,
        "source_draft_review_id": PRODUCTION_OBJECT_CANDIDATE_DRAFT_REVIEW_ID,
        "approved_count": int(validation.get("approved_count") or 0),
        "rejected_count": int(validation.get("rejected_count") or 0),
        "edited_count": int(validation.get("edited_count") or 0),
        "merged_count": int(validation.get("merged_count") or 0),
        "pending_count": int(validation.get("pending_count") or 0),
        "candidate_count": int(validation.get("candidate_count") or 0),
        "pn68_quarantined": bool(validation.get("pn68_quarantined")),
        "db_write_performed": True,
        "llm_called": False,
        "zotero_write_performed": False,
        "vector_write_performed": False,
        "object_candidates_generated": False,
        "approved_objects_created": False,
        "relation_generated": False,
        "mechanism_generated": False,
        "generation_performed": False,
        "ready_for_relation_dry_run": int(validation.get("approved_count") or 0) > 0,
        "relation_mechanism_locked": True,
        "validation": validation,
        "object_candidate_human_review_save_gate": request_gate,
        "schema_migration": schema_result,
        "saved_review": saved,
        "safety_flags": safety_flags,
        **safety_flags,
    }


def _blocked_object_candidate_human_review_save_response(
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
        "mode": "r3_phase7f_object_candidate_human_review_save",
        "schema_version": OBJECT_CANDIDATE_HUMAN_REVIEW_SCHEMA_VERSION,
        "document_id": document_id,
        "chapter_id": chapter_id,
        "reason": reason,
        "object_candidate_human_review_id": None,
        "human_review_id": None,
        "approved_count": int((validation or {}).get("approved_count") or 0),
        "rejected_count": int((validation or {}).get("rejected_count") or 0),
        "merged_count": int((validation or {}).get("merged_count") or 0),
        "pending_count": int((validation or {}).get("pending_count") or 0),
        "candidate_count": int((validation or {}).get("candidate_count") or 0),
        "db_write_performed": False,
        "llm_called": False,
        "zotero_write_performed": False,
        "vector_write_performed": False,
        "object_candidates_generated": False,
        "approved_objects_created": False,
        "relation_generated": False,
        "mechanism_generated": False,
        "generation_performed": False,
        "ready_for_relation_dry_run": False,
        "relation_mechanism_locked": True,
        "validation": validation,
        "object_candidate_human_review_save_readiness": readiness,
        "object_candidate_human_review_save_gate": request_gate,
        "safety_flags": flags,
        **flags,
    }


def _coerce_object_candidate_human_review_payload(
    review_payload: Mapping[str, Any] | list[Any] | None,
) -> dict[str, Any]:
    if isinstance(review_payload, Mapping):
        for key in [
            "human_review",
            "review_payload",
            "object_candidate_human_review",
            "object_candidate_human_review_payload",
            "payload",
        ]:
            nested = review_payload.get(key)
            if isinstance(nested, Mapping):
                return dict(nested)
        if "items" in review_payload:
            return dict(review_payload)
    if isinstance(review_payload, list):
        return {"items": list(review_payload)}
    return {"items": []}


def _phase7f_workbench_candidate(
    item: Mapping[str, Any],
    human_item: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    candidate_id = str(item.get("candidate_temp_id") or "")
    source_server_note_ids = _loads(item.get("source_server_note_ids_json"), [])
    source_labels = _loads(item.get("source_labels_json"), [])
    evidence_chunk_ids = _loads(item.get("evidence_chunk_ids_json"), [])
    page_labels = _loads(item.get("page_labels_json"), [])
    object_type = str(item.get("object_type") or "")
    confidence = item.get("confidence")
    low_confidence = _is_confidence_score(confidence) and float(confidence) <= 0.52
    suggested_action = (
        "reject"
        if object_type == "research_problem" and low_confidence
        else "approve"
        if object_type in {"concept", "method", "algorithm", "model", "metric", "dataset", "theorem_or_principle"}
        and _is_confidence_score(confidence)
        and float(confidence) >= 0.58
        else "pending"
    )
    current_action = str((human_item or {}).get("action") or item.get("review_status") or "pending_human_review")
    if current_action == "pending_human_review":
        current_action = "pending"
    return {
        "candidate_id": candidate_id,
        "candidate_temp_id": candidate_id,
        "source_draft_item_id": item.get("review_item_id"),
        "review_item_id": item.get("review_item_id"),
        "object_name": item.get("object_name"),
        "object_type": item.get("object_type"),
        "source_server_note_ids": source_server_note_ids,
        "source_labels": source_labels,
        "evidence_chunk_ids": evidence_chunk_ids,
        "page_labels": page_labels,
        "confidence": confidence,
        "rationale": item.get("rationale"),
        "duplicate_group_key": item.get("duplicate_group_key"),
        "suggested_action": suggested_action,
        "current_human_action": current_action,
        "current_object_name": (human_item or {}).get("final_object_name") or item.get("object_name"),
        "current_object_type": (human_item or {}).get("final_object_type") or item.get("object_type"),
        "merge_target_candidate_id": (human_item or {}).get("merge_target_candidate_temp_id"),
        "human_note": (human_item or {}).get("human_note") or "",
        "pn68_source": PRODUCTION_REVIEW_SECTION84_PN68_SERVER_NOTE_ID in source_server_note_ids,
        "approved_candidate": bool((human_item or {}).get("approved_candidate")),
        "relation_generated": False,
        "mechanism_generated": False,
        "zotero_write_performed": False,
        "vector_write_performed": False,
    }
