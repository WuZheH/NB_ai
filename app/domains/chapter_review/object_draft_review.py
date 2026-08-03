"""Chapter review object draft review responsibilities."""

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
)

from .normalization import (
    _hash_json_for_contract,
    _is_confidence_score,
    _utc_now,
)

from .object_candidates import (
    build_chapter_object_candidate_dry_run_package,
    validate_phase7d_object_candidate_dry_run_candidates,
)

from .object_review_common import (
    _object_candidate_payload_requests_forbidden_side_effects,
    _truthy_true,
)

from .persistence import (
    _connect_rw_existing,
    _latest_object_candidate_draft_review_row,
    _object_candidate_draft_review_schema_ready,
)

from .safety import (
    review_pipeline_safety_flags,
)

from .schema import (
    build_object_candidate_draft_review_schema_audit,
    ensure_object_candidate_draft_review_tables,
)

from .write_policy import (
    is_production_object_candidate_draft_save_enabled,
)

def validate_object_candidate_draft_save_payload(
    *,
    research_db_path: str | Path = DEFAULT_DB_PATH,
    document_id: int,
    chapter_id: int,
    dry_run_package: Mapping[str, Any] | list[Any] | None,
) -> dict[str, Any]:
    db_path = Path(research_db_path)
    errors: list[str] = []
    warnings: list[str] = []
    source_package = build_chapter_object_candidate_dry_run_package(
        research_db_path=db_path,
        document_id=document_id,
        chapter_id=chapter_id,
    )
    package = _coerce_object_candidate_draft_package(
        dry_run_package=dry_run_package,
        source_package=source_package,
    )
    candidates = list(package.get("candidates") or [])
    source_candidates = list(source_package.get("candidates") or [])
    source_candidates_by_id = {
        str(candidate.get("candidate_temp_id") or ""): candidate
        for candidate in source_candidates
        if str(candidate.get("candidate_temp_id") or "")
    }
    validator_result = validate_phase7d_object_candidate_dry_run_candidates(
        candidates=[candidate for candidate in candidates if isinstance(candidate, Mapping)],
        validator_contract=source_package.get("validator_contract") or {},
    )

    if not source_package.get("ready"):
        errors.append(str(source_package.get("reason") or "object_candidate_dry_run_not_ready"))
    if int(document_id) != PRODUCTION_OBJECT_CANDIDATE_DRAFT_DOCUMENT_ID:
        errors.append("production_object_candidate_draft_document_id_required")
    if int(chapter_id) != PRODUCTION_OBJECT_CANDIDATE_DRAFT_CHAPTER_ID:
        errors.append("production_object_candidate_draft_chapter_id_required")
    if int(package.get("document_id") or 0) != int(document_id):
        errors.append("dry_run_package_document_id_mismatch")
    if int(package.get("chapter_id") or 0) != int(chapter_id):
        errors.append("dry_run_package_chapter_id_mismatch")
    if package.get("source_classification_review_id") != PRODUCTION_OBJECT_CANDIDATE_DRAFT_SOURCE_REVIEW_ID:
        errors.append("source_classification_review_id_mismatch")
    if source_package.get("source_classification_review_id") != PRODUCTION_OBJECT_CANDIDATE_DRAFT_SOURCE_REVIEW_ID:
        errors.append("saved_classification_review_id_mismatch")
    if int(source_package.get("candidate_count") or 0) != PRODUCTION_OBJECT_CANDIDATE_DRAFT_EXPECTED_COUNT:
        errors.append("source_candidate_count_mismatch")
    if int(package.get("candidate_count") or len(candidates)) != PRODUCTION_OBJECT_CANDIDATE_DRAFT_EXPECTED_COUNT:
        errors.append("candidate_count_mismatch")
    if len(candidates) != PRODUCTION_OBJECT_CANDIDATE_DRAFT_EXPECTED_COUNT:
        errors.append("candidate_array_length_mismatch")
    if int(package.get("quarantined_count") or 0) != PRODUCTION_OBJECT_CANDIDATE_DRAFT_EXPECTED_QUARANTINED_COUNT:
        errors.append("quarantined_count_mismatch")
    if package.get("pn68_quarantined") is not True or source_package.get("pn68_quarantined") is not True:
        errors.append("pn68_quarantined_required")
    if validator_result.get("valid") is not True:
        errors.append("phase7d_candidate_validator_failed")
        errors.extend(str(error) for error in (validator_result.get("errors") or []))
    if _object_candidate_payload_requests_forbidden_side_effects(package):
        errors.append("object_candidate_draft_payload_side_effect_fields_forbidden")
    if _truthy_true(package.get("approved")) or _truthy_true(package.get("approved_objects_created")):
        errors.append("approved_objects_forbidden")

    seen_candidate_ids: set[str] = set()
    invalid_candidates: list[dict[str, Any]] = []
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, Mapping):
            errors.append(f"candidates[{index}] must be an object")
            continue
        candidate_errors: list[str] = []
        candidate_temp_id = str(candidate.get("candidate_temp_id") or "").strip()
        expected_candidate = source_candidates_by_id.get(candidate_temp_id)
        if not candidate_temp_id:
            candidate_errors.append("candidate_temp_id required")
        elif candidate_temp_id in seen_candidate_ids:
            candidate_errors.append("candidate_temp_id duplicate")
        else:
            seen_candidate_ids.add(candidate_temp_id)
        if not expected_candidate:
            candidate_errors.append("candidate_temp_id not in source dry-run package")
        else:
            for key in ["object_name", "object_type", "duplicate_group_key"]:
                if str(candidate.get(key) or "") != str(expected_candidate.get(key) or ""):
                    candidate_errors.append(f"{key} mismatch")
            for key in ["source_server_note_ids", "source_labels", "evidence_chunk_ids", "page_labels"]:
                if sorted(str(item) for item in (candidate.get(key) or [])) != sorted(str(item) for item in (expected_candidate.get(key) or [])):
                    candidate_errors.append(f"{key} mismatch")
        source_ids = [str(item).strip() for item in (candidate.get("source_server_note_ids") or []) if str(item).strip()]
        if PRODUCTION_REVIEW_SECTION84_PN68_SERVER_NOTE_ID in source_ids:
            candidate_errors.append("PN68 source candidate forbidden")
        if _truthy_true(candidate.get("approved")):
            candidate_errors.append("approved must be false")
        if _truthy_true(candidate.get("relation_generated")):
            candidate_errors.append("relation_generated must be false")
        if _truthy_true(candidate.get("mechanism_generated")):
            candidate_errors.append("mechanism_generated must be false")
        if candidate_errors:
            invalid_candidates.append(
                {
                    "index": index,
                    "candidate_temp_id": candidate_temp_id,
                    "object_name": candidate.get("object_name"),
                    "errors": candidate_errors,
                }
            )
            errors.extend(candidate_errors)

    dry_run_package_hash = _hash_json_for_contract(_object_candidate_draft_hash_payload(package))
    source_package_hash = _hash_json_for_contract(_object_candidate_draft_hash_payload(source_package))
    flags = review_pipeline_safety_flags()
    return {
        "valid": not errors and not invalid_candidates,
        "mode": "r3_phase7e_object_candidate_draft_save_validate",
        "schema_version": OBJECT_CANDIDATE_DRAFT_SAVE_SCHEMA_VERSION,
        "document_id": document_id,
        "chapter_id": chapter_id,
        "source_classification_review_id": package.get("source_classification_review_id"),
        "expected_candidate_count": PRODUCTION_OBJECT_CANDIDATE_DRAFT_EXPECTED_COUNT,
        "candidate_count": len(candidates),
        "quarantined_count": int(package.get("quarantined_count") or 0),
        "pn68_quarantined": bool(package.get("pn68_quarantined")),
        "missing_count": 0 if len(candidates) == PRODUCTION_OBJECT_CANDIDATE_DRAFT_EXPECTED_COUNT else PRODUCTION_OBJECT_CANDIDATE_DRAFT_EXPECTED_COUNT - len(candidates),
        "invalid_candidate_count": len(invalid_candidates),
        "errors": sorted(set(errors)),
        "warnings": warnings,
        "invalid_candidates": invalid_candidates,
        "dry_run_package_hash": dry_run_package_hash,
        "source_package_hash": source_package_hash,
        "phase7d_validator_result": validator_result,
        "normalized_package": package,
        "db_write_performed": False,
        "llm_called": False,
        "zotero_write_performed": False,
        "vector_write_performed": False,
        "object_candidates_generated": False,
        "relation_generated": False,
        "mechanism_generated": False,
        "generation_performed": False,
        "approved_objects_created": False,
        "safety_flags": flags,
        **flags,
    }


def build_object_candidate_draft_save_request_gate(
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
        or build_object_candidate_draft_save_readiness(research_db_path=db_path)
    )
    blockers: list[str] = list(resolved_readiness.get("current_blockers") or [])
    if int(document_id) != PRODUCTION_OBJECT_CANDIDATE_DRAFT_DOCUMENT_ID:
        blockers.append("production_object_candidate_draft_document_id_required")
    if int(chapter_id) != PRODUCTION_OBJECT_CANDIDATE_DRAFT_CHAPTER_ID:
        blockers.append("production_object_candidate_draft_chapter_id_required")
    if validation.get("valid") is not True:
        blockers.append("object_candidate_draft_validation_failed")
    if validation.get("source_classification_review_id") != PRODUCTION_OBJECT_CANDIDATE_DRAFT_SOURCE_REVIEW_ID:
        blockers.append("source_classification_review_id_mismatch")
    if int(validation.get("candidate_count") or 0) != PRODUCTION_OBJECT_CANDIDATE_DRAFT_EXPECTED_COUNT:
        blockers.append("candidate_count_mismatch")
    if int(validation.get("quarantined_count") or 0) != PRODUCTION_OBJECT_CANDIDATE_DRAFT_EXPECTED_QUARANTINED_COUNT:
        blockers.append("quarantined_count_mismatch")
    if validation.get("pn68_quarantined") is not True:
        blockers.append("pn68_quarantined_required")
    if not confirm_write:
        blockers.append("confirm_write_required")
    if confirmation_context != OBJECT_CANDIDATE_DRAFT_SAVE_CONTEXT:
        blockers.append("confirmation_context_invalid")
    if any(
        bool(validation.get(key))
        for key in [
            "llm_called",
            "zotero_write_performed",
            "vector_write_performed",
            "object_candidates_generated",
            "relation_generated",
            "mechanism_generated",
            "generation_performed",
            "approved_objects_created",
        ]
    ):
        blockers.append("object_candidate_draft_side_effect_flags_forbidden")
    existing = load_saved_object_candidate_draft_review(
        research_db_path=db_path,
        document_id=document_id,
        chapter_id=chapter_id,
        source_classification_review_id=PRODUCTION_OBJECT_CANDIDATE_DRAFT_SOURCE_REVIEW_ID,
    )
    if existing:
        blockers.append("object_candidate_draft_review_already_saved")
    blockers = list(dict.fromkeys(blockers))
    return {
        "allowed": not blockers,
        "reason": blockers[0] if blockers else None,
        "mode": "production_object_candidate_draft_save",
        "db_path": str(db_path),
        "document_id": document_id,
        "chapter_id": chapter_id,
        "draft_save_env": PRODUCTION_OBJECT_CANDIDATE_DRAFT_SAVE_ENV,
        "draft_save_env_enabled": is_production_object_candidate_draft_save_enabled(),
        "required_confirmation_context": OBJECT_CANDIDATE_DRAFT_SAVE_CONTEXT,
        "request_confirm_write": bool(confirm_write),
        "request_confirmation_context": confirmation_context,
        "source_classification_review_id": validation.get("source_classification_review_id"),
        "expected_candidate_count": PRODUCTION_OBJECT_CANDIDATE_DRAFT_EXPECTED_COUNT,
        "actual_candidate_count": int(validation.get("candidate_count") or 0),
        "quarantined_count": int(validation.get("quarantined_count") or 0),
        "pn68_quarantined": bool(validation.get("pn68_quarantined")),
        "existing_review_id": existing.get("review_id") if existing else None,
        "allowed_write_tables": list(PRODUCTION_OBJECT_CANDIDATE_DRAFT_WRITE_TABLES),
        "current_blockers": blockers,
    }


def save_chapter_object_candidate_dry_run_drafts(
    *,
    research_db_path: str | Path = DEFAULT_DB_PATH,
    document_id: int,
    chapter_id: int,
    dry_run_package: Mapping[str, Any] | list[Any] | None,
    confirm_write: bool,
    confirmation_context: str | None,
) -> dict[str, Any]:
    db_path = Path(research_db_path)
    validation = validate_object_candidate_draft_save_payload(
        research_db_path=db_path,
        document_id=document_id,
        chapter_id=chapter_id,
        dry_run_package=dry_run_package,
    )
    readiness = build_object_candidate_draft_save_readiness(research_db_path=db_path)
    request_gate = build_object_candidate_draft_save_request_gate(
        research_db_path=db_path,
        document_id=document_id,
        chapter_id=chapter_id,
        validation=validation,
        confirm_write=confirm_write,
        confirmation_context=confirmation_context,
        readiness=readiness,
    )
    if not request_gate["allowed"]:
        return _blocked_object_candidate_draft_save_response(
            document_id=document_id,
            chapter_id=chapter_id,
            reason=str(request_gate.get("reason") or "object_candidate_draft_save_blocked"),
            validation=validation,
            readiness=readiness,
            request_gate=request_gate,
        )

    schema_result = ensure_object_candidate_draft_review_tables(research_db_path=db_path, execute=True)
    package = dict(validation.get("normalized_package") or {})
    candidates = [candidate for candidate in (package.get("candidates") or []) if isinstance(candidate, Mapping)]
    safety_flags = review_pipeline_safety_flags(
        db_write_performed=True,
        schema_version=OBJECT_CANDIDATE_DRAFT_SAVE_SCHEMA_VERSION,
        object_candidate_drafts_saved=True,
        object_candidates_generated=False,
        relation_generated=False,
        mechanism_generated=False,
    )
    now = _utc_now()
    review_id = f"ocdr_{uuid4().hex}"
    dry_run_package_hash = str(validation.get("dry_run_package_hash") or "")
    source_package_hash = str(validation.get("source_package_hash") or "")

    conn = _connect_rw_existing(db_path)
    try:
        conn.row_factory = sqlite3.Row
        if not _object_candidate_draft_review_schema_ready(conn):
            return _blocked_object_candidate_draft_save_response(
                document_id=document_id,
                chapter_id=chapter_id,
                reason="object_candidate_draft_schema_missing",
                validation=validation,
                readiness=readiness,
                request_gate=request_gate,
            )
        if _latest_object_candidate_draft_review_row(
            conn,
            document_id=document_id,
            chapter_id=chapter_id,
            source_classification_review_id=PRODUCTION_OBJECT_CANDIDATE_DRAFT_SOURCE_REVIEW_ID,
        ):
            return _blocked_object_candidate_draft_save_response(
                document_id=document_id,
                chapter_id=chapter_id,
                reason="object_candidate_draft_review_already_saved",
                validation=validation,
                readiness=readiness,
                request_gate={**request_gate, "allowed": False, "current_blockers": ["object_candidate_draft_review_already_saved"]},
            )
        conn.execute(
            f"""
            INSERT INTO {OBJECT_CANDIDATE_DRAFT_REVIEW_TABLE} (
                review_id, document_id, chapter_id, review_mode,
                source_classification_review_id, source_package_hash,
                dry_run_package_hash, candidate_count, quarantined_count,
                pn68_quarantined, review_status, confirmation_context,
                safety_flags_json, llm_called, db_write_performed,
                zotero_write_performed, vector_write_performed,
                object_candidates_generated, relation_generated,
                mechanism_generated, approved_objects_created,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                review_id,
                document_id,
                chapter_id,
                "dry_run_draft_save",
                PRODUCTION_OBJECT_CANDIDATE_DRAFT_SOURCE_REVIEW_ID,
                source_package_hash,
                dry_run_package_hash,
                len(candidates),
                int(package.get("quarantined_count") or 0),
                1 if package.get("pn68_quarantined") else 0,
                "pending_human_review",
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
        for candidate in candidates:
            conn.execute(
                f"""
                INSERT INTO {OBJECT_CANDIDATE_DRAFT_REVIEW_ITEM_TABLE} (
                    review_item_id, review_id, candidate_temp_id, document_id,
                    chapter_id, object_name, object_type,
                    source_classification_review_id, source_server_note_ids_json,
                    source_labels_json, evidence_chunk_ids_json, page_labels_json,
                    confidence, rationale, duplicate_group_key, review_status,
                    approved, relation_generated, mechanism_generated, should_save,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"ocdri_{uuid4().hex}",
                    review_id,
                    str(candidate.get("candidate_temp_id") or ""),
                    document_id,
                    chapter_id,
                    str(candidate.get("object_name") or ""),
                    str(candidate.get("object_type") or ""),
                    PRODUCTION_OBJECT_CANDIDATE_DRAFT_SOURCE_REVIEW_ID,
                    json.dumps(candidate.get("source_server_note_ids") or [], ensure_ascii=False, sort_keys=True, default=str),
                    json.dumps(candidate.get("source_labels") or [], ensure_ascii=False, sort_keys=True, default=str),
                    json.dumps(candidate.get("evidence_chunk_ids") or [], ensure_ascii=False, sort_keys=True, default=str),
                    json.dumps(candidate.get("page_labels") or [], ensure_ascii=False, sort_keys=True, default=str),
                    float(candidate["confidence"]) if _is_confidence_score(candidate.get("confidence")) else None,
                    str(candidate.get("rationale") or ""),
                    str(candidate.get("duplicate_group_key") or ""),
                    "pending_human_review",
                    0,
                    0,
                    0,
                    0,
                    now,
                ),
            )
        conn.commit()
    finally:
        conn.close()

    saved = load_saved_object_candidate_draft_review(
        research_db_path=db_path,
        document_id=document_id,
        chapter_id=chapter_id,
        source_classification_review_id=PRODUCTION_OBJECT_CANDIDATE_DRAFT_SOURCE_REVIEW_ID,
    )
    return {
        "status": "saved",
        "mode": "r3_phase7e_object_candidate_draft_save",
        "schema_version": OBJECT_CANDIDATE_DRAFT_SAVE_SCHEMA_VERSION,
        "review_type": "object_candidate_draft_review",
        "document_id": document_id,
        "chapter_id": chapter_id,
        "review_mode": "dry_run_draft_save",
        "object_candidate_review_id": review_id,
        "review_id": review_id,
        "batch_id": review_id,
        "saved_candidate_count": len(candidates),
        "quarantined_count": int(package.get("quarantined_count") or 0),
        "pn68_quarantined": bool(package.get("pn68_quarantined")),
        "source_classification_review_id": PRODUCTION_OBJECT_CANDIDATE_DRAFT_SOURCE_REVIEW_ID,
        "review_status": "pending_human_review",
        "db_write_performed": True,
        "llm_called": False,
        "zotero_write_performed": False,
        "vector_write_performed": False,
        "object_candidates_generated": False,
        "approved_objects_created": False,
        "relation_generated": False,
        "mechanism_generated": False,
        "generation_performed": False,
        "ready_for_object_human_review": True,
        "relation_mechanism_locked": True,
        "object_candidate_draft_save_gate": request_gate,
        "validation": validation,
        "schema_migration": schema_result,
        "saved_review": saved,
        "safety_flags": safety_flags,
        **safety_flags,
    }


def _blocked_object_candidate_draft_save_response(
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
        "mode": "r3_phase7e_object_candidate_draft_save",
        "schema_version": OBJECT_CANDIDATE_DRAFT_SAVE_SCHEMA_VERSION,
        "review_type": "object_candidate_draft_review",
        "document_id": document_id,
        "chapter_id": chapter_id,
        "reason": reason,
        "object_candidate_review_id": None,
        "review_id": None,
        "batch_id": None,
        "saved_candidate_count": 0,
        "quarantined_count": int((validation or {}).get("quarantined_count") or 0),
        "pn68_quarantined": bool((validation or {}).get("pn68_quarantined")),
        "db_write_performed": False,
        "llm_called": False,
        "zotero_write_performed": False,
        "vector_write_performed": False,
        "object_candidates_generated": False,
        "approved_objects_created": False,
        "relation_generated": False,
        "mechanism_generated": False,
        "generation_performed": False,
        "ready_for_object_human_review": False,
        "relation_mechanism_locked": True,
        "validation": validation,
        "object_candidate_draft_save_readiness": readiness,
        "object_candidate_draft_save_gate": request_gate,
        "safety_flags": flags,
        **flags,
    }


def build_object_candidate_draft_save_readiness(
    *,
    research_db_path: str | Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    schema_audit = build_object_candidate_draft_review_schema_audit(research_db_path=research_db_path)
    blockers: list[str] = []
    if not is_production_object_candidate_draft_save_enabled():
        blockers.append("production_object_candidate_draft_save_disabled")
    flags = review_pipeline_safety_flags()
    return {
        "status": "ok",
        "mode": "r3_phase7e_object_candidate_draft_save_readiness",
        "db_path": str(Path(research_db_path)),
        "schema_ready": bool(schema_audit.get("schema_ready")),
        "schema_version": OBJECT_CANDIDATE_DRAFT_SAVE_SCHEMA_VERSION,
        "production_object_candidate_draft_save_env": PRODUCTION_OBJECT_CANDIDATE_DRAFT_SAVE_ENV,
        "production_object_candidate_draft_save_enabled": is_production_object_candidate_draft_save_enabled(),
        "production_object_candidate_draft_write_allowed": not blockers,
        "write_available": not blockers,
        "required_confirmation_context": OBJECT_CANDIDATE_DRAFT_SAVE_CONTEXT,
        "allowed_document_id": PRODUCTION_OBJECT_CANDIDATE_DRAFT_DOCUMENT_ID,
        "allowed_chapter_id": PRODUCTION_OBJECT_CANDIDATE_DRAFT_CHAPTER_ID,
        "source_classification_review_id": PRODUCTION_OBJECT_CANDIDATE_DRAFT_SOURCE_REVIEW_ID,
        "required_candidate_count": PRODUCTION_OBJECT_CANDIDATE_DRAFT_EXPECTED_COUNT,
        "required_quarantined_count": PRODUCTION_OBJECT_CANDIDATE_DRAFT_EXPECTED_QUARANTINED_COUNT,
        "allowed_write_tables": list(PRODUCTION_OBJECT_CANDIDATE_DRAFT_WRITE_TABLES),
        "current_blockers": blockers,
        "schema_audit": schema_audit,
        "safety_flags": flags,
        **flags,
    }


def _coerce_object_candidate_draft_package(
    *,
    dry_run_package: Mapping[str, Any] | list[Any] | None,
    source_package: Mapping[str, Any],
) -> dict[str, Any]:
    if isinstance(dry_run_package, Mapping):
        if isinstance(dry_run_package.get("dry_run_package"), Mapping):
            return dict(dry_run_package["dry_run_package"])
        if isinstance(dry_run_package.get("package"), Mapping):
            return dict(dry_run_package["package"])
        if isinstance(dry_run_package.get("object_candidate_dry_run_package"), Mapping):
            return dict(dry_run_package["object_candidate_dry_run_package"])
        if "candidates" in dry_run_package:
            return dict(dry_run_package)
    if isinstance(dry_run_package, list):
        package = dict(source_package)
        package["candidates"] = list(dry_run_package)
        package["candidate_count"] = len(dry_run_package)
        return package
    return dict(source_package)


def _object_candidate_draft_hash_payload(package: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": OBJECT_CANDIDATE_DRAFT_SAVE_SCHEMA_VERSION,
        "document_id": package.get("document_id"),
        "chapter_id": package.get("chapter_id"),
        "source_classification_review_id": package.get("source_classification_review_id"),
        "candidate_count": package.get("candidate_count"),
        "quarantined_count": package.get("quarantined_count"),
        "pn68_quarantined": package.get("pn68_quarantined"),
        "candidates": package.get("candidates") or [],
        "quarantined_items": package.get("quarantined_items") or [],
    }
