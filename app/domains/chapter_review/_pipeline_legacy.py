from __future__ import annotations

import json
import sqlite3
import hashlib
import os
import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

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


NOTE_CORRECTION_REVIEW_TABLE = "note_correction_reviews"
NOTE_CORRECTION_REVIEW_ITEM_TABLE = "note_correction_review_items"
NOTE_CLASSIFICATION_REVIEW_TABLE = "note_classification_reviews"
NOTE_CLASSIFICATION_REVIEW_ITEM_TABLE = "note_classification_review_items"
NOTE_CORRECTION_SAVE_CONTEXT = "save_note_correction_review_after_user_audit"
NOTE_CLASSIFICATION_SAVE_CONTEXT = "save_note_classification_review_after_manual_json_validation"
OBJECT_CANDIDATE_DRAFT_SAVE_CONTEXT = "save_object_candidate_drafts_after_dry_run_review"
OBJECT_CANDIDATE_HUMAN_REVIEW_SAVE_CONTEXT = "save_object_candidate_human_review_after_user_audit"
NOTE_CORRECTION_REVIEW_SAVE_SCHEMA_VERSION = "r3_note_correction_review_save_v1"
NOTE_CORRECTION_HUMAN_AUDIT_SCHEMA_VERSION = "r3_note_correction_human_audit_v1"
NOTE_CLASSIFICATION_REVIEW_SAVE_SCHEMA_VERSION = "r3_note_classification_review_save_v1"
OBJECT_CANDIDATE_DRAFT_SAVE_SCHEMA_VERSION = "r3_object_candidate_draft_save_v1"
OBJECT_CANDIDATE_HUMAN_REVIEW_SCHEMA_VERSION = "r3_object_candidate_human_review_v1"
PRODUCTION_DB_WRITE_ENABLED = False
PRODUCTION_REVIEW_SAVE_CANARY_ENV = "NOTEBOOK_AI_ENABLE_PRODUCTION_REVIEW_SAVE_CANARY"
PRODUCTION_REVIEW_SAVE_SECTION_ENV = "NOTEBOOK_AI_ENABLE_PRODUCTION_REVIEW_SECTION_SAVE"
PRODUCTION_REVIEW_SAVE_SECTION84_PN68_ENV = "NOTEBOOK_AI_ENABLE_PRODUCTION_REVIEW_SECTION84_PN68_SAVE"
PRODUCTION_REVIEW_SAVE_SECTION_TARGET_ENV = "NOTEBOOK_AI_PRODUCTION_REVIEW_SECTION_SAVE_TARGET"
PRODUCTION_NOTE_CLASSIFICATION_SAVE_ENV = "NOTEBOOK_AI_ENABLE_PRODUCTION_NOTE_CLASSIFICATION_SAVE"
PRODUCTION_OBJECT_CANDIDATE_DRAFT_SAVE_ENV = "NOTEBOOK_AI_ENABLE_PRODUCTION_OBJECT_CANDIDATE_DRAFT_SAVE"
PRODUCTION_OBJECT_CANDIDATE_HUMAN_REVIEW_SAVE_ENV = "NOTEBOOK_AI_ENABLE_PRODUCTION_OBJECT_CANDIDATE_HUMAN_REVIEW_SAVE"
OBJECT_CANDIDATE_DRAFT_REVIEW_TABLE = "object_candidate_draft_reviews"
OBJECT_CANDIDATE_DRAFT_REVIEW_ITEM_TABLE = "object_candidate_draft_review_items"
OBJECT_CANDIDATE_HUMAN_REVIEW_TABLE = "object_candidate_human_reviews"
OBJECT_CANDIDATE_HUMAN_REVIEW_ITEM_TABLE = "object_candidate_human_review_items"
PRODUCTION_REVIEW_CANARY_WRITE_TABLES = (
    NOTE_CORRECTION_REVIEW_TABLE,
    NOTE_CORRECTION_REVIEW_ITEM_TABLE,
)
PRODUCTION_NOTE_CLASSIFICATION_WRITE_TABLES = (
    NOTE_CLASSIFICATION_REVIEW_TABLE,
    NOTE_CLASSIFICATION_REVIEW_ITEM_TABLE,
)
PRODUCTION_OBJECT_CANDIDATE_DRAFT_WRITE_TABLES = (
    OBJECT_CANDIDATE_DRAFT_REVIEW_TABLE,
    OBJECT_CANDIDATE_DRAFT_REVIEW_ITEM_TABLE,
)
PRODUCTION_OBJECT_CANDIDATE_HUMAN_REVIEW_WRITE_TABLES = (
    OBJECT_CANDIDATE_HUMAN_REVIEW_TABLE,
    OBJECT_CANDIDATE_HUMAN_REVIEW_ITEM_TABLE,
)
PRODUCTION_REVIEW_SECTION_DOCUMENT_ID = 10
PRODUCTION_REVIEW_SECTION_CHAPTER_ID = 69
PRODUCTION_REVIEW_SECTION_ALLOWED_SCOPES = {
    "section_8_2": 10,
    "section_8_5": 5,
    "section_8_6": 8,
    "section_8_7": 12,
}
PRODUCTION_REVIEW_SECTION_DEFERRED_SCOPES = {
    "section_8_3": "already_saved",
    "section_8_4": "pn68_deferred",
}
PRODUCTION_REVIEW_SECTION84_PN68_SCOPE_ID = "section_8_4"
PRODUCTION_REVIEW_SECTION84_PN68_EXPECTED_COUNT = 24
PRODUCTION_REVIEW_SECTION84_PN68_ZOTERO_KEY = "PN68YPTT"
PRODUCTION_REVIEW_SECTION84_PN68_SERVER_NOTE_ID = "zinsp_zotero_annotation_13b3ac182e7d3975ba7623eea1dbb288"
PRODUCTION_REVIEW_SECTION84_PN68_ALLOWED_STATUSES = {"unclear", "needs_revision"}
PRODUCTION_REVIEW_SECTION84_PN68_REQUIRED_WARNINGS = {
    "bbox_present_no_readable_layout_anchor",
    "document_resolved_but_no_page_text_match",
    "alignment_uncertain",
}
PRODUCTION_OBJECT_CANDIDATE_DRAFT_DOCUMENT_ID = 10
PRODUCTION_OBJECT_CANDIDATE_DRAFT_CHAPTER_ID = 69
PRODUCTION_OBJECT_CANDIDATE_DRAFT_SOURCE_REVIEW_ID = "nclr_1595f273202e46069d8ba946778eb885"
PRODUCTION_OBJECT_CANDIDATE_DRAFT_EXPECTED_COUNT = 37
PRODUCTION_OBJECT_CANDIDATE_DRAFT_EXPECTED_QUARANTINED_COUNT = 1
PRODUCTION_OBJECT_CANDIDATE_DRAFT_REVIEW_ID = "ocdr_2f8908674f7b4e85931bda71f473006e"
PRODUCTION_OBJECT_CANDIDATE_HUMAN_REVIEW_ID = "ochr_35325ffb80714a4bae96b6411e29ae08"
MERGED_NOTE_CORRECTION_SECTION_ORDER = (
    "section_8_2",
    "section_8_3",
    "section_8_4",
    "section_8_5",
    "section_8_6",
    "section_8_7",
)

NOTE_CLASSIFICATION_LABEL_ORDER = (
    "memory_note",
    "connection_note",
    "mechanism_note",
    "research_idea_note",
    "unclear",
    "needs_manual_review",
)
NOTE_CLASSIFICATION_LABELS = set(NOTE_CLASSIFICATION_LABEL_ORDER)
NOTE_CLASSIFICATION_MANUAL_CONFIDENCE_ORDER = ("low", "medium", "high")
NOTE_CLASSIFICATION_MANUAL_CONFIDENCES = set(NOTE_CLASSIFICATION_MANUAL_CONFIDENCE_ORDER)
OBJECT_CANDIDATE_DRY_RUN_TYPE_ORDER = (
    "concept",
    "method",
    "algorithm",
    "model",
    "metric",
    "dataset",
    "theorem_or_principle",
    "mechanism_candidate",
    "research_problem",
    "experiment_candidate",
)
OBJECT_CANDIDATE_DRY_RUN_TYPES = set(OBJECT_CANDIDATE_DRY_RUN_TYPE_ORDER)
OBJECT_CANDIDATE_DRY_RUN_QUARANTINE_LABELS = {"unclear", "needs_manual_review"}
RELATION_CANDIDATE_DRY_RUN_SCHEMA_VERSION = "r3_relation_candidate_dry_run_v1"
RELATION_CANDIDATE_VALIDATOR_CONTRACT_VERSION = "r3_relation_candidate_validator_contract_v1"
RELATION_CANDIDATE_DRY_RUN_TYPE_ORDER = (
    "related_to",
    "contrasts_with",
    "supports",
    "refines",
    "uses_method",
    "has_component",
    "part_of",
    "evaluates_with_metric",
    "evaluated_on_dataset",
    "addresses_problem",
    "suggests_mechanism",
    "inspires_research_idea",
)
RELATION_CANDIDATE_DRY_RUN_TYPES = set(RELATION_CANDIDATE_DRY_RUN_TYPE_ORDER)
USER_TAG_AGREEMENTS = {
    "agrees",
    "disagrees",
    "partially_agrees",
    "no_user_type_tag",
}


def review_pipeline_safety_flags(**overrides: Any) -> dict[str, Any]:
    flags = note_correction_dry_run_safety_flags()
    flags.update(
        {
            "db_write_performed": False,
            "core_db_write_performed": False,
            "zotero_db_write_performed": False,
            "vector_store_write_performed": False,
            "llm_called": False,
            "external_llm_called": False,
            "object_candidates_generated": False,
            "relation_candidates_generated": False,
            "relation_generated": False,
            "insight_cards_generated": False,
            "mechanism_generated": False,
            "mechanism_draft_written": False,
            "ocr_or_marker_performed": False,
        }
    )
    flags.update(overrides)
    if "core_db_write_performed" not in overrides and "db_write_performed" in overrides:
        flags["core_db_write_performed"] = bool(flags["db_write_performed"])
    return flags


def is_production_review_save_canary_enabled() -> bool:
    return os.getenv(PRODUCTION_REVIEW_SAVE_CANARY_ENV) == "1"


def is_production_review_save_section_enabled() -> bool:
    return os.getenv(PRODUCTION_REVIEW_SAVE_SECTION_ENV) == "1"


def is_production_review_save_section84_pn68_enabled() -> bool:
    return os.getenv(PRODUCTION_REVIEW_SAVE_SECTION84_PN68_ENV) == "1"


def is_production_note_classification_save_enabled() -> bool:
    return os.getenv(PRODUCTION_NOTE_CLASSIFICATION_SAVE_ENV) == "1"


def is_production_object_candidate_draft_save_enabled() -> bool:
    return os.getenv(PRODUCTION_OBJECT_CANDIDATE_DRAFT_SAVE_ENV) == "1"


def is_production_object_candidate_human_review_save_enabled() -> bool:
    return os.getenv(PRODUCTION_OBJECT_CANDIDATE_HUMAN_REVIEW_SAVE_ENV) == "1"


def production_review_save_section_target() -> str:
    return str(os.getenv(PRODUCTION_REVIEW_SAVE_SECTION_TARGET_ENV) or "").strip()


def production_review_save_section_target_expected_count(target: str | None = None) -> int | None:
    resolved_target = str(target if target is not None else production_review_save_section_target()).strip()
    return PRODUCTION_REVIEW_SECTION_ALLOWED_SCOPES.get(resolved_target)


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


def build_chapter_note_classification_package(
    *,
    research_db_path: str | Path = DEFAULT_DB_PATH,
    document_id: int,
    chapter_id: int,
) -> dict[str, Any]:
    correction_package = build_chapter_note_correction_prompt_package(
        research_db_path=research_db_path,
        document_id=document_id,
        chapter_id=chapter_id,
    )
    saved = load_merged_saved_note_correction_review(
        research_db_path=research_db_path,
        document_id=document_id,
        chapter_id=chapter_id,
        correction_package=correction_package,
    )
    if not saved:
        return _blocked_classification_package(
            document_id=document_id,
            chapter_id=chapter_id,
            correction_package=correction_package,
            reason="note_correction_review_not_saved",
        )
    if not saved.get("ready_for_classification"):
        return _blocked_classification_package(
            document_id=document_id,
            chapter_id=chapter_id,
            correction_package=correction_package,
            reason="note_correction_review_merge_incomplete",
            note_correction_review_saved=True,
        )
    followup_count = sum(
        1
        for item in saved.get("review_items", [])
        if item.get("human_action") == "needs_followup"
    )
    if followup_count:
        return _blocked_classification_package(
            document_id=document_id,
            chapter_id=chapter_id,
            correction_package=correction_package,
            reason="needs_followup_items",
            note_correction_review_saved=True,
            needs_followup_count=followup_count,
        )

    corrected_notes = _corrected_notes_for_classification(correction_package, saved)
    package = {
        "status": "note_classification_package_ready",
        "ready": True,
        "mode": "r3_note_classification_package_dry_run",
        "document_id": document_id,
        "chapter_id": chapter_id,
        "document": correction_package.get("document") or {},
        "unit": correction_package.get("unit") or {},
        "chapter_context": correction_package.get("chapter_context") or {},
        "chapter_context_summary": _chapter_context_summary(correction_package),
        "note_anchors": correction_package.get("note_anchors") or [],
        "corrected_notes": corrected_notes,
        "classification_candidates": corrected_notes,
        "candidate_count": len(corrected_notes),
        "item_count": len(corrected_notes),
        "unique_server_note_ids": len({
            str(note.get("server_note_id") or "").strip()
            for note in corrected_notes
            if str(note.get("server_note_id") or "").strip()
        }),
        "supporting_evidence": correction_package.get("supporting_evidence") or [],
        "supporting_evidence_count": len(correction_package.get("supporting_evidence") or []),
        "classification_taxonomy": classification_taxonomy(),
        "output_schema": note_classification_output_schema(),
        "review_pipeline": {
            "current_gate": "note_classification_review",
            "previous_gate": "note_correction_review",
            "next_gate": "tri_source_object_candidate_package",
            "required_gates": [
                "note_correction_review",
                "note_classification_review",
                "object_review",
                "mechanism_review",
            ],
        },
        "system_instructions": [
            "Classify only corrected/reviewed user notes.",
            "Evidence-only annotations remain supporting_evidence and must not become classification candidates.",
            "Do not generate object_candidates, relation_candidates, mechanisms, or insights.",
            "Return JSON with review_type=note_classification_review.",
        ],
        "copy_ready_prompt": "",
        "note_correction_review_saved": True,
        "note_classification_review_saved": False,
        "note_classification_generated": False,
        "classification_generated": False,
        "source_review_count": saved.get("source_review_count", 0),
        "source_section_review_count": saved.get("source_section_review_count", 0),
        "canary_audit_count": saved.get("canary_audit_count", 0),
        "source_review_ids": saved.get("source_review_ids", []),
        "source_section_ids": saved.get("source_section_ids", []),
        "duplicate_review_items_ignored": saved.get("duplicate_review_items_ignored", []),
        "canary_items_shadowed": saved.get("canary_items_shadowed", []),
        "pn68_status": saved.get("pn68_status", {}),
        "merged_saved_review": saved.get("merged_metadata", {}),
        "zotero_write_performed": False,
        "vector_write_performed": False,
        "generation_performed": False,
        **review_pipeline_safety_flags(),
    }
    package["copy_ready_prompt"] = build_note_classification_copy_ready_prompt(package)
    return package


def build_chapter_note_classification_dry_run_package(
    *,
    research_db_path: str | Path = DEFAULT_DB_PATH,
    document_id: int,
    chapter_id: int,
) -> dict[str, Any]:
    package = build_chapter_note_classification_package(
        research_db_path=research_db_path,
        document_id=document_id,
        chapter_id=chapter_id,
    )
    flags = review_pipeline_safety_flags()
    notes = list(package.get("classification_candidates") or [])
    section_distribution = _classification_section_distribution(notes)
    pn68 = _phase7a_pn68_status(notes, package.get("pn68_status") or {})
    validator_contract = build_phase7a_classification_validator_contract(
        package=package,
        section_distribution=section_distribution,
        pn68=pn68,
    )
    note_summaries = _phase7a_note_summaries(notes)
    ready = bool(package.get("ready"))
    source_package_hash = _hash_json_for_contract(package)
    saved_classification = load_saved_note_classification_review(
        research_db_path=research_db_path,
        document_id=document_id,
        chapter_id=chapter_id,
    )
    classification_saved = bool(saved_classification)
    dry_run_package = {
        "status": "classification_dry_run_package_ready" if ready else "blocked",
        "ready": ready,
        "mode": "r3_phase7a_classification_dry_run_review",
        "document_id": document_id,
        "chapter_id": chapter_id,
        "source": "merged_saved_note_correction_review",
        "source_package_status": package.get("status"),
        "source_package_hash": source_package_hash,
        "item_count": int(package.get("item_count") or len(notes)),
        "unique_server_note_ids": int(package.get("unique_server_note_ids") or 0),
        "source_review_count": package.get("source_review_count", 0),
        "source_section_review_count": package.get("source_section_review_count", 0),
        "canary_audit_count": package.get("canary_audit_count", 0),
        "source_review_ids": package.get("source_review_ids", []),
        "source_section_ids": package.get("source_section_ids", []),
        "section_distribution": section_distribution,
        "expected_section_distribution": {
            "section_8_2": 10,
            "section_8_3": 8,
            "section_8_4": 24,
            "section_8_5": 5,
            "section_8_6": 8,
            "section_8_7": 12,
        },
        "classification_taxonomy": classification_taxonomy(),
        "taxonomy_audit": classification_taxonomy_audit(),
        "allowed_labels": list(NOTE_CLASSIFICATION_LABEL_ORDER),
        "output_schema": note_classification_output_schema(),
        "validator_contract": validator_contract,
        "pn68": pn68,
        "prompt_preview_available": ready,
        "note_summaries": note_summaries,
        "classification_candidates": notes,
        "copy_ready_prompt": "",
        "dry_run_only": True,
        "note_classification_review_saved": classification_saved,
        "classification_review_saved": classification_saved,
        "classification_review_status": saved_classification.get("status") if saved_classification else "not_saved",
        "classification_review_id": saved_classification.get("review_id") if saved_classification else None,
        "classification_saved_item_count": saved_classification.get("saved_item_count") if saved_classification else 0,
        "classification_label_counts": saved_classification.get("label_counts") if saved_classification else {},
        "classification_confidence_counts": saved_classification.get("confidence_counts") if saved_classification else {},
        "pn68_classification_label": saved_classification.get("pn68_classification_label") if saved_classification else None,
        "pn68_classification_confidence": saved_classification.get("pn68_confidence") if saved_classification else None,
        "ready_for_object_candidate_generation": bool(saved_classification),
        "object_candidate_generation_status": "requires_explicit_phase7d_gate" if saved_classification else "blocked_note_classification_not_saved",
        "note_classification_generated": False,
        "classification_generated": False,
        "object_candidates_generated": False,
        "relation_candidates_generated": False,
        "mechanism_generated": False,
        "generation_performed": False,
        "zotero_write_performed": False,
        "vector_write_performed": False,
        "db_write_performed": False,
        "llm_called": False,
        "phase7b_gate_conditions": [
            "Use explicit controlled-generation gate; Phase7A does not call LLM.",
            "Validate output against validator_contract before any save is considered.",
            "Keep PN68 as unclear or needs_manual_review unless its warning is explicitly handled.",
            "Do not generate object/relation/mechanism candidates in the classification layer.",
            "Keep any future save behind an explicit no-write dry-run and production gate.",
        ],
        "safety_flags": flags,
        **flags,
    }
    dry_run_package["copy_ready_prompt"] = build_phase7a_classification_prompt_preview(dry_run_package)
    return dry_run_package


def validate_chapter_note_classification_review(
    *,
    research_db_path: str | Path = DEFAULT_DB_PATH,
    document_id: int,
    chapter_id: int,
    review_payload: str | Mapping[str, Any],
) -> dict[str, Any]:
    package = build_chapter_note_classification_package(
        research_db_path=research_db_path,
        document_id=document_id,
        chapter_id=chapter_id,
    )
    errors: list[str] = []
    warnings: list[str] = []
    if not package.get("ready"):
        errors.append(str(package.get("reason") or "note_classification_package_not_ready"))
        candidates = []
    else:
        candidates = list(package.get("classification_candidates") or [])

    candidate_index = _classification_candidate_index(candidates)
    expected_keys = {
        _classification_candidate_key(candidate)
        for candidate in candidates
        if _classification_candidate_key(candidate)
    }
    parsed = _parse_review_payload(review_payload, errors)
    normalized_items: list[dict[str, Any]] = []
    seen: set[str] = set()

    if parsed is not None:
        forbidden = sorted(_forbidden_keys(parsed))
        if forbidden:
            errors.append(f"forbidden review keys present: {', '.join(forbidden)}")
        if parsed.get("review_type") != "note_classification_review":
            errors.append("review_type must be note_classification_review")
        if _int_or_none(parsed.get("document_id")) != int(document_id):
            errors.append(f"document_id must be {document_id}")
        if _int_or_none(parsed.get("chapter_id")) != int(chapter_id):
            errors.append(f"chapter_id must be {chapter_id}")
        raw_items = parsed.get("items")
        if not isinstance(raw_items, list):
            errors.append("items must be an array")
            raw_items = []
        if len(raw_items) != len(candidates):
            errors.append(f"items count must be {len(candidates)}")
        for index, raw_item in enumerate(raw_items):
            normalized, matched_key = _normalize_classification_item(
                raw_item,
                index=index,
                candidate_index=candidate_index,
                errors=errors,
                warnings=warnings,
            )
            if normalized:
                normalized_items.append(normalized)
            if matched_key:
                if matched_key in seen:
                    errors.append(f"items[{index}] duplicates candidate {matched_key}")
                seen.add(matched_key)
        missing = sorted(expected_keys - seen)
        if missing:
            errors.append(f"items missing expected candidates: {', '.join(missing[:8])}")
        stats = _classification_stats(normalized_items, expected_count=len(candidates))
        _validate_classification_summary(parsed.get("summary"), stats, errors)
    else:
        stats = _classification_stats([], expected_count=len(candidates))

    flags = review_pipeline_safety_flags()
    return {
        "status": "ok",
        "mode": "note_classification_review_validate_dry_run",
        "review_type": "note_classification_review",
        "document_id": document_id,
        "chapter_id": chapter_id,
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "stats": stats,
        "normalized_preview": normalized_items,
        "validation_note": (
            "校验通过，但本阶段不会保存笔记分类审核结果。"
            if not errors
            else "校验失败；本阶段不会写入任何审核结果。"
        ),
        "safety_flags": flags,
        **flags,
    }


def validate_chapter_note_classification_manual_json(
    *,
    research_db_path: str | Path = DEFAULT_DB_PATH,
    document_id: int,
    chapter_id: int,
    classification_payload: str | Mapping[str, Any] | list[Any],
) -> dict[str, Any]:
    package = build_chapter_note_classification_dry_run_package(
        research_db_path=research_db_path,
        document_id=document_id,
        chapter_id=chapter_id,
    )
    flags = review_pipeline_safety_flags()
    errors: list[str] = []
    warnings: list[str] = []
    invalid_items: list[dict[str, Any]] = []
    preview_items: list[dict[str, Any]] = []

    parsed = _parse_manual_classification_payload(classification_payload, errors)
    if not package.get("ready"):
        errors.append(str(package.get("reason") or "classification_dry_run_package_not_ready"))
    candidates = list(package.get("classification_candidates") or [])
    expected_by_server_id = {
        str(candidate.get("server_note_id") or "").strip(): candidate
        for candidate in candidates
        if str(candidate.get("server_note_id") or "").strip()
    }
    expected_server_ids = set(expected_by_server_id)

    source_package_hash_status = "missing"
    raw_items: list[Any] = []
    forbidden = sorted(_manual_forbidden_keys(parsed))
    if forbidden:
        errors.append(f"forbidden manual classification keys present: {', '.join(forbidden)}")
    if isinstance(parsed, Mapping):
        if _int_or_none(parsed.get("document_id")) != int(document_id):
            errors.append(f"document_id must be {document_id}")
        if _int_or_none(parsed.get("chapter_id")) != int(chapter_id):
            errors.append(f"chapter_id must be {chapter_id}")
        source_hash = _str_or_none(parsed.get("source_package_hash"))
        expected_hash = _str_or_none(package.get("source_package_hash"))
        if not source_hash:
            source_package_hash_status = "missing_with_warning"
            warnings.append("source_package_hash missing; manual validation continues with exact server_note_id coverage")
        elif expected_hash and source_hash != expected_hash:
            source_package_hash_status = "mismatch"
            errors.append("source_package_hash does not match current dry-run package")
        else:
            source_package_hash_status = "matched"
        raw_items_value = parsed.get("items")
        if isinstance(raw_items_value, list):
            raw_items = raw_items_value
        else:
            errors.append("items must be an array")
    elif parsed is not None:
        errors.append("manual classification JSON must be an object")

    seen: set[str] = set()
    duplicate_server_note_ids: list[str] = []
    unexpected_server_note_ids: list[str] = []
    invalid_label_count = 0
    invalid_confidence_count = 0
    preserve_original_note_text_fail_count = 0
    rationale_missing_count = 0
    pn68_seen = False
    pn68_item: dict[str, Any] | None = None
    pn68_errors: list[str] = []
    pn68_warnings: list[str] = []

    for index, raw_item in enumerate(raw_items):
        item_errors: list[str] = []
        item_warnings: list[str] = []
        if not isinstance(raw_item, Mapping):
            item_errors.append("item must be an object")
            normalized = {
                "index": index,
                "server_note_id": None,
                "note_type": None,
                "confidence": None,
                "rationale": "",
                "warnings": [],
            }
        else:
            server_note_id = _str_or_none(raw_item.get("server_note_id"))
            note_type = _str_or_none(raw_item.get("note_type") or raw_item.get("primary_type"))
            confidence = _str_or_none(raw_item.get("confidence"))
            rationale = str(raw_item.get("rationale") or raw_item.get("classification_rationale") or "").strip()
            warning_list = _manual_warning_list(raw_item.get("warnings"))
            candidate = expected_by_server_id.get(server_note_id or "")
            if not server_note_id:
                item_errors.append("server_note_id is required")
            elif server_note_id not in expected_server_ids:
                item_errors.append("server_note_id is unexpected")
                unexpected_server_note_ids.append(server_note_id)
            elif server_note_id in seen:
                item_errors.append("server_note_id is duplicated")
                duplicate_server_note_ids.append(server_note_id)
            else:
                seen.add(server_note_id)
            if note_type not in NOTE_CLASSIFICATION_LABELS:
                item_errors.append("note_type is invalid")
                invalid_label_count += 1
            if confidence not in NOTE_CLASSIFICATION_MANUAL_CONFIDENCES:
                item_errors.append("confidence is invalid")
                invalid_confidence_count += 1
            if note_type in {"mechanism_note", "research_idea_note", "needs_manual_review"} and not rationale:
                item_errors.append("rationale is required for this note_type")
                rationale_missing_count += 1
            if raw_item.get("preserve_original_note_text") is not True:
                item_errors.append("preserve_original_note_text must be true")
                preserve_original_note_text_fail_count += 1
            is_pn68 = (
                server_note_id == PRODUCTION_REVIEW_SECTION84_PN68_SERVER_NOTE_ID
                or (candidate or {}).get("zotero_annotation_key") == PRODUCTION_REVIEW_SECTION84_PN68_ZOTERO_KEY
            )
            if is_pn68:
                pn68_seen = True
                pn68_item = {
                    "index": index,
                    "server_note_id": server_note_id,
                    "note_type": note_type,
                    "confidence": confidence,
                    "warnings": warning_list,
                }
                has_alignment_warning = any(
                    token in str(warning).lower()
                    for warning in warning_list
                    for token in ["alignment_uncertain", "unmatched", "alignment", "manual_review"]
                )
                if note_type == "mechanism_note":
                    item_errors.append("PN68 cannot be classified as mechanism_note")
                    pn68_errors.append("PN68 cannot be classified as mechanism_note")
                if confidence == "high":
                    item_errors.append("PN68 cannot use high confidence")
                    pn68_errors.append("PN68 cannot use high confidence")
                if note_type not in {"unclear", "needs_manual_review", "memory_note", "connection_note"}:
                    item_errors.append("PN68 note_type must be unclear, needs_manual_review, or low-confidence memory/connection")
                    pn68_errors.append("PN68 note_type is outside recommended handling")
                if note_type in {"memory_note", "connection_note"} and confidence != "low":
                    item_errors.append("PN68 memory/connection classification must be low confidence")
                    pn68_errors.append("PN68 memory/connection classification must be low confidence")
                if not has_alignment_warning:
                    item_errors.append("PN68 warnings must include alignment_uncertain or unmatched")
                    pn68_errors.append("PN68 warnings must include alignment_uncertain or unmatched")
            normalized = {
                "index": index,
                "server_note_id": server_note_id,
                "note_type": note_type,
                "confidence": confidence,
                "rationale": rationale,
                "warnings": warning_list,
                "source_section_id": (candidate or {}).get("source_section_id"),
                "zotero_annotation_key": (candidate or {}).get("zotero_annotation_key") or raw_item.get("zotero_annotation_key"),
                "page": (candidate or {}).get("page") or raw_item.get("page"),
                "matched_chunk_id": (candidate or {}).get("matched_chunk_id"),
                "original_note_text_excerpt": _excerpt((candidate or {}).get("original_note_text"), 220),
                "selected_text_excerpt": _excerpt((candidate or {}).get("selected_text"), 220),
                "valid": not item_errors,
            }
        preview_items.append(normalized)
        if item_errors:
            invalid_items.append(
                {
                    **normalized,
                    "errors": item_errors,
                    "warnings": item_warnings,
                }
            )

    missing_server_note_ids = sorted(expected_server_ids - seen)
    if len(raw_items) != len(candidates):
        errors.append(f"items count must be {len(candidates)}")
    if missing_server_note_ids:
        errors.append(f"items missing expected server_note_id: {', '.join(missing_server_note_ids[:8])}")
    if duplicate_server_note_ids:
        errors.append(f"items duplicate server_note_id: {', '.join(sorted(set(duplicate_server_note_ids))[:8])}")
    if unexpected_server_note_ids:
        errors.append(f"items unexpected server_note_id: {', '.join(sorted(set(unexpected_server_note_ids))[:8])}")
    if not pn68_seen:
        errors.append("PN68 item is missing")
        pn68_errors.append("PN68 item is missing")

    label_counts = dict(Counter(str(item.get("note_type") or "") for item in preview_items if item.get("note_type")))
    confidence_counts = dict(Counter(str(item.get("confidence") or "") for item in preview_items if item.get("confidence")))
    stats = {
        "expected_item_count": len(candidates),
        "item_count": len(raw_items),
        "validated_item_count": len(preview_items),
        "missing_count": len(missing_server_note_ids),
        "duplicate_count": len(duplicate_server_note_ids),
        "unexpected_count": len(unexpected_server_note_ids),
        "invalid_label_count": invalid_label_count,
        "invalid_confidence_count": invalid_confidence_count,
        "preserve_original_note_text_fail_count": preserve_original_note_text_fail_count,
        "rationale_missing_count": rationale_missing_count,
        "invalid_item_count": len(invalid_items),
    }
    pn68_validation = {
        "present": pn68_seen,
        "valid": pn68_seen and not pn68_errors,
        "recommended_handling": "unclear_or_needs_manual_review",
        "item": pn68_item,
        "errors": sorted(set(pn68_errors)),
        "warnings": pn68_warnings,
    }
    return {
        "status": "ok",
        "mode": "r3_phase7b_manual_classification_json_validate_preview",
        "review_type": "note_classification_manual_json",
        "document_id": document_id,
        "chapter_id": chapter_id,
        "valid": not errors and not invalid_items,
        "ready_for_phase7c_save_gate": False,
        "errors": errors,
        "warnings": warnings,
        "stats": stats,
        "item_count": len(raw_items),
        "expected_item_count": len(candidates),
        "missing_count": len(missing_server_note_ids),
        "duplicate_count": len(duplicate_server_note_ids),
        "unexpected_count": len(unexpected_server_note_ids),
        "invalid_label_count": invalid_label_count,
        "label_counts": label_counts,
        "confidence_counts": confidence_counts,
        "pn68_validation": pn68_validation,
        "source_package_hash_status": source_package_hash_status,
        "expected_source_package_hash": package.get("source_package_hash"),
        "missing_server_note_ids": missing_server_note_ids,
        "duplicate_server_note_ids": sorted(set(duplicate_server_note_ids)),
        "unexpected_server_note_ids": sorted(set(unexpected_server_note_ids)),
        "invalid_items": invalid_items,
        "preview_items": preview_items,
        "expected_json_schema": phase7b_manual_classification_expected_schema(),
        "zotero_write_performed": False,
        "vector_write_performed": False,
        "generation_performed": False,
        "validation_note": (
            "Manual classification JSON is valid for preview; Phase7B still does not save."
            if not errors and not invalid_items
            else "Manual classification JSON is invalid; Phase7B did not write anything."
        ),
        "safety_flags": flags,
        **flags,
    }


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


def build_chapter_object_candidate_dry_run_package(
    *,
    research_db_path: str | Path = DEFAULT_DB_PATH,
    document_id: int,
    chapter_id: int,
) -> dict[str, Any]:
    saved = load_saved_note_classification_review(
        research_db_path=research_db_path,
        document_id=document_id,
        chapter_id=chapter_id,
    )
    flags = review_pipeline_safety_flags()
    if not saved:
        return {
            "status": "blocked",
            "ready": False,
            "mode": "r3_phase7d_object_candidate_dry_run_package",
            "document_id": document_id,
            "chapter_id": chapter_id,
            "reason": "note_classification_review_not_saved",
            "candidate_count": 0,
            "quarantined_count": 0,
            "pn68_quarantined": False,
            "candidates": [],
            "quarantined_items": [],
            "validator_contract": build_phase7d_object_candidate_validator_contract(
                expected_server_note_ids=[],
                pn68_server_note_id=PRODUCTION_REVIEW_SECTION84_PN68_SERVER_NOTE_ID,
            ),
            "safety_flags": flags,
            **flags,
        }

    items = list(saved.get("items") or [])
    label_counts = Counter(str(item.get("classification_label") or "") for item in items)
    label_distribution = {label: int(label_counts.get(label, 0)) for label in NOTE_CLASSIFICATION_LABEL_ORDER}
    server_ids = [str(item.get("server_note_id") or "").strip() for item in items if str(item.get("server_note_id") or "").strip()]
    unique_server_ids = sorted(set(server_ids))
    quarantined_items: list[dict[str, Any]] = []
    candidates_by_key: dict[str, dict[str, Any]] = {}
    pn68_quarantined = False

    for item in items:
        label = str(item.get("classification_label") or "").strip()
        server_note_id = str(item.get("server_note_id") or "").strip()
        is_pn68 = (
            server_note_id == PRODUCTION_REVIEW_SECTION84_PN68_SERVER_NOTE_ID
            or item.get("zotero_annotation_key") == PRODUCTION_REVIEW_SECTION84_PN68_ZOTERO_KEY
        )
        if is_pn68 or label in OBJECT_CANDIDATE_DRY_RUN_QUARANTINE_LABELS:
            quarantined_items.append(_phase7d_quarantined_item(item, reason="pn68_quarantine" if is_pn68 else f"{label}_quarantine"))
            pn68_quarantined = pn68_quarantined or is_pn68
            continue
        for candidate in _phase7d_candidates_for_classified_item(item):
            key = str(candidate["duplicate_group_key"])
            existing = candidates_by_key.get(key)
            if existing:
                existing_sources = existing.setdefault("source_server_note_ids", [])
                for source_id in candidate["source_server_note_ids"]:
                    if source_id not in existing_sources:
                        existing_sources.append(source_id)
                existing_labels = existing.setdefault("source_labels", [])
                for source_label in candidate["source_labels"]:
                    if source_label not in existing_labels:
                        existing_labels.append(source_label)
                existing_chunks = existing.setdefault("evidence_chunk_ids", [])
                for chunk_id in candidate.get("evidence_chunk_ids") or []:
                    if chunk_id not in existing_chunks:
                        existing_chunks.append(chunk_id)
                existing_pages = existing.setdefault("page_labels", [])
                for page_label in candidate.get("page_labels") or []:
                    if page_label not in existing_pages:
                        existing_pages.append(page_label)
                existing["rationale"] = f"{existing['rationale']} | additional source: {server_note_id}"
                continue
            candidates_by_key[key] = candidate

    candidates = sorted(
        candidates_by_key.values(),
        key=lambda item: (
            OBJECT_CANDIDATE_DRY_RUN_TYPE_ORDER.index(str(item.get("object_type")))
            if str(item.get("object_type")) in OBJECT_CANDIDATE_DRY_RUN_TYPES
            else 999,
            str(item.get("object_name") or ""),
        ),
    )
    for index, candidate in enumerate(candidates, start=1):
        seed = f"{candidate['duplicate_group_key']}|{','.join(candidate['source_server_note_ids'])}"
        candidate["candidate_temp_id"] = f"ocdry_{hashlib.sha1(seed.encode('utf-8')).hexdigest()[:12]}"
        candidate["source_server_note_ids"] = sorted(candidate["source_server_note_ids"])
        candidate["source_labels"] = sorted(candidate["source_labels"])
        candidate["evidence_chunk_ids"] = sorted(candidate.get("evidence_chunk_ids") or [])
        candidate["page_labels"] = sorted(candidate.get("page_labels") or [])

    validator_contract = build_phase7d_object_candidate_validator_contract(
        expected_server_note_ids=unique_server_ids,
        pn68_server_note_id=PRODUCTION_REVIEW_SECTION84_PN68_SERVER_NOTE_ID,
    )
    validation = validate_phase7d_object_candidate_dry_run_candidates(
        candidates=candidates,
        validator_contract=validator_contract,
    )
    prompt_preview = build_phase7d_object_candidate_prompt_preview(
        package_summary={
            "document_id": document_id,
            "chapter_id": chapter_id,
            "source_classification_review_id": saved.get("review_id"),
            "source_item_count": len(items),
            "label_distribution": label_distribution,
            "candidate_count": len(candidates),
            "quarantined_count": len(quarantined_items),
            "pn68_quarantined": pn68_quarantined,
        },
        validator_contract=validator_contract,
    )
    saved_draft_review = load_saved_object_candidate_draft_review(
        research_db_path=research_db_path,
        document_id=document_id,
        chapter_id=chapter_id,
        source_classification_review_id=str(saved.get("review_id") or ""),
    )
    saved_human_review = None
    if saved_draft_review:
        saved_human_review = load_saved_object_candidate_human_review(
            research_db_path=research_db_path,
            document_id=document_id,
            chapter_id=chapter_id,
            source_draft_review_id=str(saved_draft_review.get("review_id") or ""),
        )
    save_status = (
        "human_review_saved_relation_locked"
        if saved_human_review
        else "drafts_saved_pending_human_review"
        if saved_draft_review
        else "locked_future_phase7e_gate"
    )
    return {
        "status": "object_candidate_dry_run_ready",
        "ready": True,
        "mode": "r3_phase7d_object_candidate_dry_run_package",
        "document_id": document_id,
        "chapter_id": chapter_id,
        "source_classification_review_id": saved.get("review_id"),
        "source_item_count": len(items),
        "unique_server_note_ids": len(unique_server_ids),
        "label_distribution": label_distribution,
        "candidate_count": len(candidates),
        "quarantined_count": len(quarantined_items),
        "pn68_quarantined": pn68_quarantined,
        "allowed_object_types": list(OBJECT_CANDIDATE_DRY_RUN_TYPE_ORDER),
        "extraction_policy": phase7d_object_candidate_extraction_policy(),
        "candidates": candidates,
        "quarantined_items": quarantined_items,
        "validator_contract": validator_contract,
        "validator_result": validation,
        "prompt_preview": prompt_preview,
        "save_forbidden_until_phase7e_gate": not bool(saved_draft_review),
        "object_candidate_save_status": save_status,
        "object_candidate_draft_review_status": saved_draft_review.get("status") if saved_draft_review else "not_saved",
        "object_candidate_draft_review_id": saved_draft_review.get("review_id") if saved_draft_review else None,
        "object_candidate_draft_saved_count": saved_draft_review.get("saved_candidate_count") if saved_draft_review else 0,
        "saved_draft_review": saved_draft_review,
        "object_candidate_human_review_status": saved_human_review.get("status") if saved_human_review else "not_saved",
        "object_candidate_human_review_id": saved_human_review.get("human_review_id") if saved_human_review else None,
        "object_candidate_human_review_saved_count": saved_human_review.get("saved_item_count") if saved_human_review else 0,
        "approved_candidate_count": saved_human_review.get("approved_count") if saved_human_review else 0,
        "rejected_candidate_count": saved_human_review.get("rejected_count") if saved_human_review else 0,
        "merged_candidate_count": saved_human_review.get("merged_count") if saved_human_review else 0,
        "pending_candidate_count": saved_human_review.get("pending_count") if saved_human_review else 0,
        "ready_for_relation_dry_run": bool(saved_human_review and int(saved_human_review.get("approved_count") or 0) > 0),
        "saved_human_review": saved_human_review,
        "relation_layer_status": "locked_relation_dry_run_not_started" if saved_human_review else "locked_objects_not_reviewed",
        "mechanism_layer_status": "locked_objects_and_relations_not_reviewed",
        "db_write_performed": False,
        "llm_called": False,
        "object_candidates_generated": False,
        "relation_generated": False,
        "mechanism_generated": False,
        "zotero_write_performed": False,
        "vector_write_performed": False,
        "generation_performed": False,
        "safety_flags": flags,
        **flags,
    }


def _phase7d_quarantined_item(item: Mapping[str, Any], *, reason: str) -> dict[str, Any]:
    special_flags = _loads(item.get("special_flags_json"), {})
    warnings = special_flags.get("warnings") or []
    return {
        "server_note_id": item.get("server_note_id"),
        "client_note_id": item.get("client_note_id"),
        "zotero_annotation_key": item.get("zotero_annotation_key"),
        "section_id": item.get("section_id"),
        "page_label": item.get("page_label"),
        "classification_label": item.get("classification_label"),
        "confidence": item.get("confidence"),
        "reason": reason,
        "pn68": bool(special_flags.get("pn68"))
        or item.get("server_note_id") == PRODUCTION_REVIEW_SECTION84_PN68_SERVER_NOTE_ID
        or item.get("zotero_annotation_key") == PRODUCTION_REVIEW_SECTION84_PN68_ZOTERO_KEY,
        "warnings": warnings,
        "matched_chunk_id": special_flags.get("matched_chunk_id"),
        "note_text_excerpt": _phase7d_excerpt(item.get("original_note_text"), limit=180),
        "selected_text_excerpt": _phase7d_excerpt(item.get("selected_text"), limit=220),
        "should_extract_object_candidate": False,
    }


def _phase7d_candidates_for_classified_item(item: Mapping[str, Any]) -> list[dict[str, Any]]:
    label = str(item.get("classification_label") or "").strip()
    server_note_id = str(item.get("server_note_id") or "").strip()
    if not server_note_id or label in OBJECT_CANDIDATE_DRY_RUN_QUARANTINE_LABELS:
        return []
    text = "\n".join(
        str(value or "")
        for value in [
            item.get("original_note_text"),
            item.get("selected_text"),
            item.get("rationale"),
        ]
    )
    matched_terms = _phase7d_known_object_terms_for_text(text)
    if label == "memory_note":
        # Memory notes only become candidates when they explicitly mention an
        # established concept, method, model, metric, theorem, or dataset.
        return [_phase7d_candidate_from_term(item, term, label=label) for term in matched_terms]

    candidates = [_phase7d_candidate_from_term(item, term, label=label) for term in matched_terms]
    if not candidates and label == "mechanism_note":
        fallback = _phase7d_fallback_note_candidate(
            item,
            label=label,
            object_type="mechanism_candidate",
            prefix="Mechanism",
        )
        if fallback:
            candidates.append(fallback)
    if not candidates and label == "research_idea_note":
        fallback = _phase7d_fallback_note_candidate(
            item,
            label=label,
            object_type="research_problem",
            prefix="Research problem",
        )
        if fallback:
            candidates.append(fallback)
    return candidates


def _phase7d_candidate_from_term(
    item: Mapping[str, Any],
    term: Mapping[str, str],
    *,
    label: str,
) -> dict[str, Any]:
    special_flags = _loads(item.get("special_flags_json"), {})
    server_note_id = str(item.get("server_note_id") or "").strip()
    object_name = str(term["object_name"])
    object_type = str(term["object_type"])
    return {
        "candidate_temp_id": None,
        "object_name": object_name,
        "object_type": object_type,
        "source_server_note_ids": [server_note_id],
        "source_labels": [label],
        "evidence_chunk_ids": _phase7d_chunk_ids(special_flags),
        "page_labels": _phase7d_page_labels(item),
        "confidence": _phase7d_confidence_for_label(label, matched_known_term=True),
        "rationale": (
            f"Dry-run extraction from explicit term '{object_name}' in a saved "
            f"{label}; no object row will be saved in Phase7D."
        ),
        "duplicate_group_key": f"{object_type}:{_phase7d_slug(object_name)}",
        "should_save": False,
    }


def _phase7d_fallback_note_candidate(
    item: Mapping[str, Any],
    *,
    label: str,
    object_type: str,
    prefix: str,
) -> dict[str, Any] | None:
    phrase = _phase7d_note_phrase(item)
    if not phrase:
        return None
    special_flags = _loads(item.get("special_flags_json"), {})
    server_note_id = str(item.get("server_note_id") or "").strip()
    object_name = f"{prefix}: {phrase}"
    return {
        "candidate_temp_id": None,
        "object_name": object_name,
        "object_type": object_type,
        "source_server_note_ids": [server_note_id],
        "source_labels": [label],
        "evidence_chunk_ids": _phase7d_chunk_ids(special_flags),
        "page_labels": _phase7d_page_labels(item),
        "confidence": _phase7d_confidence_for_label(label, matched_known_term=False),
        "rationale": (
            f"Dry-run note-derived {object_type} from saved {label}; requires "
            "manual object review before any future save."
        ),
        "duplicate_group_key": f"{object_type}:{_phase7d_slug(object_name)}",
        "should_save": False,
    }


def _phase7d_known_object_terms_for_text(text: str) -> list[dict[str, str]]:
    matched: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for term in _phase7d_known_object_terms():
        pattern = str(term["pattern"])
        if re.search(pattern, text, flags=re.IGNORECASE):
            key = (str(term["object_name"]), str(term["object_type"]))
            if key not in seen:
                seen.add(key)
                matched.append(
                    {
                        "object_name": str(term["object_name"]),
                        "object_type": str(term["object_type"]),
                    }
                )
    return matched


def _phase7d_known_object_terms() -> list[dict[str, str]]:
    return [
        {"pattern": r"梯度下降|steepest descent|gradient descent", "object_name": "Gradient descent", "object_type": "algorithm"},
        {"pattern": r"共轭梯度|conjugate gradient", "object_name": "Conjugate gradient", "object_type": "algorithm"},
        {"pattern": r"牛顿法|Newton", "object_name": "Newton method", "object_type": "algorithm"},
        {"pattern": r"拟牛顿|quasi[- ]?Newton|BFGS|L-BFGS", "object_name": "Quasi-Newton method", "object_type": "algorithm"},
        {"pattern": r"Nesterov|NAG|Nesterov动量", "object_name": "Nesterov momentum", "object_type": "algorithm"},
        {"pattern": r"动量法|momentum", "object_name": "Momentum method", "object_type": "algorithm"},
        {"pattern": r"线搜索|line search", "object_name": "Line search", "object_type": "method"},
        {"pattern": r"信任区域|trust region", "object_name": "Trust-region method", "object_type": "method"},
        {"pattern": r"Hessian|Hession|海森|H矩阵", "object_name": "Hessian matrix", "object_type": "concept"},
        {"pattern": r"正定矩阵|positive definite", "object_name": "Positive definite matrix", "object_type": "concept"},
        {"pattern": r"条件数|condition number", "object_name": "Condition number", "object_type": "metric"},
        {"pattern": r"二次型|quadratic form|二次函数", "object_name": "Quadratic form", "object_type": "model"},
        {"pattern": r"特征值|eigenvalue|λmax|lambda", "object_name": "Eigenvalue", "object_type": "concept"},
        {"pattern": r"收敛速度|convergence rate", "object_name": "Convergence rate", "object_type": "metric"},
        {"pattern": r"全局收敛|global convergence", "object_name": "Global convergence", "object_type": "theorem_or_principle"},
        {"pattern": r"步长|step size|learning rate", "object_name": "Step size", "object_type": "metric"},
        {"pattern": r"局部最优|local optimum|局部最大", "object_name": "Local optimum", "object_type": "concept"},
        {"pattern": r"Wolfe", "object_name": "Wolfe condition", "object_type": "theorem_or_principle"},
        {"pattern": r"Armijo", "object_name": "Armijo rule", "object_type": "method"},
        {"pattern": r"Z字|zig[- ]?zag", "object_name": "Zig-zag convergence", "object_type": "mechanism_candidate"},
    ]


def _phase7d_note_phrase(item: Mapping[str, Any]) -> str:
    text = str(item.get("original_note_text") or "").strip()
    if not text:
        text = str(item.get("selected_text") or "").strip()
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return ""
    return text[:48].rstrip()


def _phase7d_excerpt(value: Any, *, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit].rstrip()


def _phase7d_chunk_ids(special_flags: Mapping[str, Any]) -> list[int]:
    chunk_id = _int_or_none(special_flags.get("matched_chunk_id"))
    return [chunk_id] if chunk_id is not None else []


def _phase7d_page_labels(item: Mapping[str, Any]) -> list[str]:
    page_label = str(item.get("page_label") or item.get("pdf_page") or "").strip()
    return [page_label] if page_label else []


def _phase7d_confidence_for_label(label: str, *, matched_known_term: bool) -> float:
    if not matched_known_term:
        return 0.52
    if label == "mechanism_note":
        return 0.68
    if label == "research_idea_note":
        return 0.64
    if label == "connection_note":
        return 0.62
    return 0.58


def _phase7d_slug(value: str) -> str:
    normalized = re.sub(r"\s+", " ", value.strip().lower())
    normalized = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "-", normalized).strip("-")
    return normalized or hashlib.sha1(value.encode("utf-8")).hexdigest()[:10]


def phase7d_object_candidate_extraction_policy() -> dict[str, Any]:
    return {
        "memory_note": "Default no candidate unless known concept/method/algorithm/model/metric/theorem term appears.",
        "connection_note": "Extract concept/method/model connection candidates from explicit terms.",
        "mechanism_note": "Extract mechanism_candidate plus supporting concepts/methods from explicit terms.",
        "research_idea_note": "Extract research_problem or experiment_candidate from explicit terms or note-derived idea phrase.",
        "unclear": "Quarantine; no automatic object candidate.",
        "needs_manual_review": "Quarantine; no automatic object candidate.",
        "PN68": "Always quarantine from automatic object extraction.",
        "persistence": "Dry-run only; should_save=false for every candidate.",
    }


def build_phase7d_object_candidate_validator_contract(
    *,
    expected_server_note_ids: list[str],
    pn68_server_note_id: str,
) -> dict[str, Any]:
    return {
        "schema_version": "r3_phase7d_object_candidate_validator_contract_v1",
        "source": "saved_note_classification_review",
        "allowed_object_types": list(OBJECT_CANDIDATE_DRY_RUN_TYPE_ORDER),
        "expected_server_note_ids": sorted(expected_server_note_ids),
        "pn68_server_note_id": pn68_server_note_id,
        "rules": [
            "every candidate must link to at least one source_server_note_id",
            "every source_server_note_id must exist in classification review",
            "no candidate from PN68 unless explicitly manual_override=true",
            "object_type must be in allowed_object_types",
            "duplicate_group_key required",
            "original note_text must not be overwritten or embedded as replacement text",
            "relation/mechanism generation is forbidden in this layer",
            "save is forbidden unless future Phase7E gate is enabled",
        ],
        "no_write_boundary": {
            "db_write_allowed": False,
            "object_candidate_save_allowed": False,
            "relation_generation_allowed": False,
            "mechanism_generation_allowed": False,
            "zotero_write_allowed": False,
            "vector_write_allowed": False,
            "llm_allowed": False,
        },
    }


def validate_phase7d_object_candidate_dry_run_candidates(
    *,
    candidates: list[Mapping[str, Any]],
    validator_contract: Mapping[str, Any],
) -> dict[str, Any]:
    expected = set(str(item) for item in (validator_contract.get("expected_server_note_ids") or []))
    allowed_types = set(str(item) for item in (validator_contract.get("allowed_object_types") or []))
    pn68_server_note_id = str(validator_contract.get("pn68_server_note_id") or "")
    errors: list[str] = []
    invalid_candidates: list[dict[str, Any]] = []
    for index, candidate in enumerate(candidates):
        candidate_errors: list[str] = []
        object_type = str(candidate.get("object_type") or "")
        source_ids = [str(item).strip() for item in (candidate.get("source_server_note_ids") or []) if str(item).strip()]
        if not source_ids:
            candidate_errors.append("source_server_note_ids required")
        unknown = sorted(set(source_ids) - expected)
        if unknown:
            candidate_errors.append(f"unknown source_server_note_id: {', '.join(unknown[:5])}")
        if pn68_server_note_id in source_ids and candidate.get("manual_override") is not True:
            candidate_errors.append("PN68 source requires manual_override=true")
        if object_type not in allowed_types:
            candidate_errors.append("object_type invalid")
        if not str(candidate.get("duplicate_group_key") or "").strip():
            candidate_errors.append("duplicate_group_key required")
        if "original_note_text" in candidate or "relation_candidates" in candidate or "mechanism_candidates" in candidate:
            candidate_errors.append("forbidden candidate fields present")
        if candidate.get("should_save") is not False:
            candidate_errors.append("should_save must be false in dry-run")
        if candidate_errors:
            invalid_candidates.append({
                "index": index,
                "candidate_temp_id": candidate.get("candidate_temp_id"),
                "object_name": candidate.get("object_name"),
                "errors": candidate_errors,
            })
            errors.extend(candidate_errors)
    flags = review_pipeline_safety_flags()
    return {
        "valid": not invalid_candidates,
        "candidate_count": len(candidates),
        "invalid_candidate_count": len(invalid_candidates),
        "errors": sorted(set(errors)),
        "invalid_candidates": invalid_candidates,
        "db_write_performed": False,
        "llm_called": False,
        "object_candidates_generated": False,
        "relation_generated": False,
        "mechanism_generated": False,
        "zotero_write_performed": False,
        "vector_write_performed": False,
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


def build_chapter_relation_candidate_dry_run_package(
    *,
    research_db_path: str | Path = DEFAULT_DB_PATH,
    document_id: int,
    chapter_id: int,
) -> dict[str, Any]:
    db_path = Path(research_db_path)
    flags = _relation_dry_run_safety_flags()
    saved_human_review = load_saved_object_candidate_human_review(
        research_db_path=db_path,
        document_id=document_id,
        chapter_id=chapter_id,
        source_draft_review_id=PRODUCTION_OBJECT_CANDIDATE_DRAFT_REVIEW_ID,
    )
    if not saved_human_review:
        contract = build_phase7g_relation_candidate_validator_contract(
            source_object_candidate_human_review_id=None,
            approved_candidates=[],
            excluded_candidates={"rejected": [], "pending": [], "merged": []},
        )
        return {
            "status": "blocked",
            "ready": False,
            "mode": "r3_phase7g_relation_candidate_dry_run_package",
            "schema_version": RELATION_CANDIDATE_DRY_RUN_SCHEMA_VERSION,
            "document_id": document_id,
            "chapter_id": chapter_id,
            "reason": "object_candidate_human_review_not_saved",
            "source_object_candidate_human_review_id": None,
            "approved_source_candidate_count": 0,
            "excluded_rejected_count": 0,
            "excluded_pending_count": 0,
            "excluded_merged_count": 0,
            "pn68_excluded": False,
            "pn68_source_candidate_count": 0,
            "relation_candidate_count": 0,
            "relation_candidates": [],
            "excluded_candidates": {"rejected": [], "pending": [], "merged": []},
            "validator_contract": contract,
            "validator_result": validate_relation_candidate_dry_run_package(
                research_db_path=db_path,
                document_id=document_id,
                chapter_id=chapter_id,
                relation_package={"relation_candidates": []},
                validator_contract=contract,
            ),
            "relation_save_status": "locked_object_candidate_human_review_not_saved",
            "mechanism_layer_status": "locked_objects_and_relations_not_reviewed",
            "safety_flags": flags,
            **flags,
        }

    source_candidates = [
        _phase7g_relation_source_candidate(item)
        for item in (saved_human_review.get("items") or [])
        if isinstance(item, Mapping)
    ]
    approved_candidates = [
        candidate
        for candidate in source_candidates
        if candidate.get("approved_candidate") is True
        and str(candidate.get("current_human_action") or "") in {"approve", "edit"}
    ]
    rejected_candidates = [
        candidate for candidate in source_candidates if str(candidate.get("current_human_action") or "") == "reject"
    ]
    pending_candidates = [
        candidate for candidate in source_candidates if str(candidate.get("current_human_action") or "") == "pending"
    ]
    merged_candidates = [
        candidate for candidate in source_candidates if str(candidate.get("current_human_action") or "") == "merge"
    ]
    excluded_candidates = {
        "rejected": [_phase7g_excluded_candidate_summary(candidate, reason="rejected") for candidate in rejected_candidates],
        "pending": [_phase7g_excluded_candidate_summary(candidate, reason="pending") for candidate in pending_candidates],
        "merged": [_phase7g_excluded_candidate_summary(candidate, reason="merged") for candidate in merged_candidates],
    }
    pn68_source_candidate_count = sum(1 for candidate in source_candidates if candidate.get("pn68_source"))
    approved_missing_sources = [
        candidate.get("candidate_id")
        for candidate in approved_candidates
        if not candidate.get("source_server_note_ids")
    ]
    approved_invalid_types = [
        candidate.get("candidate_id")
        for candidate in approved_candidates
        if str(candidate.get("object_type") or "") not in OBJECT_CANDIDATE_DRY_RUN_TYPES
    ]
    relation_candidates = _phase7g_relation_candidates_for_approved(
        approved_candidates,
        source_object_candidate_human_review_id=str(saved_human_review.get("human_review_id") or ""),
    )
    contract = build_phase7g_relation_candidate_validator_contract(
        source_object_candidate_human_review_id=str(saved_human_review.get("human_review_id") or ""),
        approved_candidates=approved_candidates,
        excluded_candidates=excluded_candidates,
    )
    package: dict[str, Any] = {
        "status": "relation_candidate_dry_run_ready",
        "ready": True,
        "mode": "r3_phase7g_relation_candidate_dry_run_package",
        "schema_version": RELATION_CANDIDATE_DRY_RUN_SCHEMA_VERSION,
        "document_id": document_id,
        "chapter_id": chapter_id,
        "source_object_candidate_human_review_id": saved_human_review.get("human_review_id"),
        "expected_source_object_candidate_human_review_id": PRODUCTION_OBJECT_CANDIDATE_HUMAN_REVIEW_ID,
        "source_object_candidate_human_review_matches_expected": (
            saved_human_review.get("human_review_id") == PRODUCTION_OBJECT_CANDIDATE_HUMAN_REVIEW_ID
        ),
        "source_object_candidate_draft_review_id": saved_human_review.get("source_draft_review_id"),
        "approved_source_candidate_count": len(approved_candidates),
        "excluded_rejected_count": len(rejected_candidates),
        "excluded_pending_count": len(pending_candidates),
        "excluded_merged_count": len(merged_candidates),
        "pn68_excluded": pn68_source_candidate_count == 0,
        "pn68_source_candidate_count": pn68_source_candidate_count,
        "approved_candidates_all_have_source_server_note_ids": not approved_missing_sources,
        "approved_candidates_missing_source_server_note_ids": approved_missing_sources,
        "approved_candidates_all_have_allowed_object_type": not approved_invalid_types,
        "approved_candidates_invalid_object_type_ids": approved_invalid_types,
        "allowed_relation_types": list(RELATION_CANDIDATE_DRY_RUN_TYPE_ORDER),
        "relation_candidate_count": len(relation_candidates),
        "relation_candidates": relation_candidates,
        "relation_extraction_policy": phase7g_relation_candidate_extraction_policy(),
        "excluded_candidates": excluded_candidates,
        "validator_contract": contract,
        "relation_save_status": "future_phase7h_gate_required",
        "save_relation_disabled": True,
        "save_forbidden_until_phase7h_gate": True,
        "mechanism_layer_status": "locked_relations_not_reviewed",
        "mechanism_locked": True,
        "db_write_performed": False,
        "relation_rows_written": False,
        "relation_generated": False,
        "mechanism_generated": False,
        "object_registry_write_performed": False,
        "llm_called": False,
        "zotero_write_performed": False,
        "vector_write_performed": False,
        "generation_performed": False,
        "safety_flags": flags,
        **flags,
    }
    package["validator_result"] = validate_relation_candidate_dry_run_package(
        research_db_path=db_path,
        document_id=document_id,
        chapter_id=chapter_id,
        relation_package=package,
        validator_contract=contract,
    )
    package["prompt_preview"] = build_phase7g_relation_candidate_prompt_preview(
        package_summary={
            "document_id": document_id,
            "chapter_id": chapter_id,
            "source_object_candidate_human_review_id": saved_human_review.get("human_review_id"),
            "approved_source_candidate_count": len(approved_candidates),
            "excluded_rejected_count": len(rejected_candidates),
            "excluded_pending_count": len(pending_candidates),
            "relation_candidate_count": len(relation_candidates),
            "pn68_excluded": pn68_source_candidate_count == 0,
        },
        validator_contract=contract,
        relation_candidates=relation_candidates,
    )
    return package


def build_phase7g_relation_candidate_validator_contract(
    *,
    source_object_candidate_human_review_id: str | None,
    approved_candidates: list[Mapping[str, Any]],
    excluded_candidates: Mapping[str, list[Mapping[str, Any]]],
) -> dict[str, Any]:
    approved_ids = sorted(
        str(candidate.get("candidate_id") or candidate.get("candidate_temp_id") or "")
        for candidate in approved_candidates
        if str(candidate.get("candidate_id") or candidate.get("candidate_temp_id") or "")
    )
    approved_source_ids = sorted({
        str(source_id)
        for candidate in approved_candidates
        for source_id in (candidate.get("source_server_note_ids") or [])
        if str(source_id).strip()
    })
    excluded_ids = sorted({
        str(candidate.get("candidate_id") or "")
        for group in (excluded_candidates or {}).values()
        for candidate in group
        if str(candidate.get("candidate_id") or "")
    })
    return {
        "schema_version": RELATION_CANDIDATE_VALIDATOR_CONTRACT_VERSION,
        "source": "saved_object_candidate_human_review",
        "source_object_candidate_human_review_id": source_object_candidate_human_review_id,
        "expected_source_object_candidate_human_review_id": PRODUCTION_OBJECT_CANDIDATE_HUMAN_REVIEW_ID,
        "source_object_candidate_draft_review_id": PRODUCTION_OBJECT_CANDIDATE_DRAFT_REVIEW_ID,
        "approved_source_candidate_count": len(approved_ids),
        "approved_candidate_ids": approved_ids,
        "approved_source_server_note_ids": approved_source_ids,
        "excluded_candidate_ids": excluded_ids,
        "excluded_rejected_candidate_ids": sorted(
            str(candidate.get("candidate_id") or "")
            for candidate in (excluded_candidates.get("rejected") or [])
            if str(candidate.get("candidate_id") or "")
        ),
        "excluded_pending_candidate_ids": sorted(
            str(candidate.get("candidate_id") or "")
            for candidate in (excluded_candidates.get("pending") or [])
            if str(candidate.get("candidate_id") or "")
        ),
        "excluded_merged_candidate_ids": sorted(
            str(candidate.get("candidate_id") or "")
            for candidate in (excluded_candidates.get("merged") or [])
            if str(candidate.get("candidate_id") or "")
        ),
        "pn68_server_note_id": PRODUCTION_REVIEW_SECTION84_PN68_SERVER_NOTE_ID,
        "allowed_relation_types": list(RELATION_CANDIDATE_DRY_RUN_TYPE_ORDER),
        "rules": [
            "every relation candidate must use approved source candidates only",
            "rejected, pending, and merged source candidates are forbidden in relation candidates",
            "PN68 source note is forbidden in relation candidates",
            "self relation is forbidden",
            "relation_type must be in allowed_relation_types",
            "source_server_note_ids must exist and be tied to approved candidates",
            "evidence_chunk_ids are optional, but every present value must be integer-like",
            "confidence must be between 0 and 1 and must come from evidence proximity rules",
            "should_save must be false in Phase7G dry-run",
            "mechanism fields must not be generated",
            "DB write, relation row write, object registry write, Zotero write, vector write, and LLM flags must be false",
        ],
        "no_write_boundary": {
            "db_write_allowed": False,
            "relation_row_write_allowed": False,
            "object_registry_write_allowed": False,
            "mechanism_generation_allowed": False,
            "zotero_write_allowed": False,
            "vector_write_allowed": False,
            "llm_allowed": False,
        },
    }


def validate_relation_candidate_dry_run_package(
    *,
    research_db_path: str | Path = DEFAULT_DB_PATH,
    document_id: int,
    chapter_id: int,
    relation_package: Mapping[str, Any] | None,
    validator_contract: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    package = dict(relation_package or {})
    contract = dict(validator_contract or package.get("validator_contract") or {})
    if not contract:
        source_package = build_chapter_relation_candidate_dry_run_package(
            research_db_path=research_db_path,
            document_id=document_id,
            chapter_id=chapter_id,
        )
        contract = dict(source_package.get("validator_contract") or {})
    allowed_relation_types = set(str(item) for item in (contract.get("allowed_relation_types") or []))
    approved_candidate_ids = set(str(item) for item in (contract.get("approved_candidate_ids") or []))
    approved_source_note_ids = set(str(item) for item in (contract.get("approved_source_server_note_ids") or []))
    excluded_candidate_ids = set(str(item) for item in (contract.get("excluded_candidate_ids") or []))
    pn68_server_note_id = str(contract.get("pn68_server_note_id") or "")
    relation_candidates = list(package.get("relation_candidates") or [])
    errors: list[str] = []
    invalid_candidates: list[dict[str, Any]] = []

    if package.get("document_id") not in (None, document_id):
        errors.append("document_id mismatch")
    if package.get("chapter_id") not in (None, chapter_id):
        errors.append("chapter_id mismatch")
    if package.get("source_object_candidate_human_review_id") and contract.get("source_object_candidate_human_review_id"):
        if package.get("source_object_candidate_human_review_id") != contract.get("source_object_candidate_human_review_id"):
            errors.append("source_object_candidate_human_review_id mismatch")
    for key in [
        "db_write_performed",
        "relation_rows_written",
        "relation_generated",
        "relation_candidates_generated",
        "mechanism_generated",
        "object_registry_write_performed",
        "object_candidates_generated",
        "llm_called",
        "zotero_write_performed",
        "vector_write_performed",
        "generation_performed",
    ]:
        if bool(package.get(key)):
            errors.append(f"{key} must be false")

    for index, candidate in enumerate(relation_candidates):
        if not isinstance(candidate, Mapping):
            invalid_candidates.append({"index": index, "relation_temp_id": None, "errors": ["relation candidate must be object"]})
            continue
        candidate_errors: list[str] = []
        relation_type = str(candidate.get("relation_type") or "")
        subject_id = str(candidate.get("subject_candidate_id") or "")
        object_id = str(candidate.get("object_candidate_id") or "")
        source_object_ids = [
            str(item)
            for item in (candidate.get("source_object_candidate_ids") or [])
            if str(item).strip()
        ]
        source_note_ids = [
            str(item)
            for item in (candidate.get("source_server_note_ids") or [])
            if str(item).strip()
        ]
        chunk_ids = candidate.get("evidence_chunk_ids") or []
        if relation_type not in allowed_relation_types:
            candidate_errors.append("relation_type invalid")
        if not subject_id or not object_id:
            candidate_errors.append("subject_candidate_id and object_candidate_id required")
        if subject_id == object_id:
            candidate_errors.append("self relation forbidden")
        for source_id in [subject_id, object_id, *source_object_ids]:
            if source_id not in approved_candidate_ids:
                candidate_errors.append(f"source candidate must be approved: {source_id}")
            if source_id in excluded_candidate_ids:
                candidate_errors.append(f"rejected_or_pending_source_candidate_forbidden: {source_id}")
        if not source_note_ids:
            candidate_errors.append("source_server_note_ids required")
        unknown_source_notes = sorted(set(source_note_ids) - approved_source_note_ids)
        if unknown_source_notes:
            candidate_errors.append(f"unknown source_server_note_id: {', '.join(unknown_source_notes[:5])}")
        if pn68_server_note_id and pn68_server_note_id in source_note_ids:
            candidate_errors.append("PN68 source forbidden")
        if not isinstance(chunk_ids, list):
            candidate_errors.append("evidence_chunk_ids must be a list")
        else:
            invalid_chunks = [item for item in chunk_ids if _int_or_none(item) is None]
            if invalid_chunks:
                candidate_errors.append("evidence_chunk_ids must be integer-like")
        if not _is_confidence_score(candidate.get("confidence")):
            candidate_errors.append("confidence must be between 0 and 1")
        if candidate.get("review_status") != "dry_run_only":
            candidate_errors.append("review_status must be dry_run_only")
        if candidate.get("should_save") is not False:
            candidate_errors.append("should_save must be false in dry-run")
        if any(key in candidate for key in ["mechanism_payload", "mechanism_draft"]) or bool(candidate.get("mechanism_generated")):
            candidate_errors.append("mechanism fields forbidden")
        if candidate_errors:
            invalid_candidates.append({
                "index": index,
                "relation_temp_id": candidate.get("relation_temp_id"),
                "errors": sorted(set(candidate_errors)),
            })
            errors.extend(candidate_errors)

    flags = _relation_dry_run_safety_flags()
    return {
        "valid": not errors and not invalid_candidates,
        "schema_version": RELATION_CANDIDATE_VALIDATOR_CONTRACT_VERSION,
        "document_id": document_id,
        "chapter_id": chapter_id,
        "source_object_candidate_human_review_id": contract.get("source_object_candidate_human_review_id"),
        "relation_candidate_count": len(relation_candidates),
        "approved_source_candidate_count": len(approved_candidate_ids),
        "invalid_candidate_count": len(invalid_candidates),
        "errors": sorted(set(errors)),
        "invalid_candidates": invalid_candidates,
        "db_write_performed": False,
        "relation_rows_written": False,
        "relation_generated": False,
        "mechanism_generated": False,
        "object_registry_write_performed": False,
        "llm_called": False,
        "zotero_write_performed": False,
        "vector_write_performed": False,
        "safety_flags": flags,
        **flags,
    }


def build_phase7g_relation_candidate_prompt_preview(
    *,
    package_summary: Mapping[str, Any],
    validator_contract: Mapping[str, Any],
    relation_candidates: list[Mapping[str, Any]],
) -> str:
    preview = relation_candidates[:12]
    return "\n".join(
        [
            "# NOTEBOOK_AI Phase7G relation candidate dry-run prompt preview",
            "",
            "This preview is for later manual review only. NOTEBOOK_AI did not call an LLM in Phase7G.",
            "",
            "## Task boundary",
            "Use approved object candidates only. Propose relation candidates only.",
            "Do not save relation rows. Do not generate mechanisms. Do not write Zotero, vector store, object registry, or production DB.",
            "",
            "## Package summary",
            json.dumps(package_summary, ensure_ascii=False, indent=2, sort_keys=True),
            "",
            "## Validator contract",
            json.dumps(validator_contract, ensure_ascii=False, indent=2, sort_keys=True),
            "",
            "## Relation candidate preview",
            json.dumps(preview, ensure_ascii=False, indent=2, sort_keys=True, default=str),
        ]
    )


def phase7g_relation_candidate_extraction_policy() -> dict[str, Any]:
    return {
        "source": "saved Phase7F object_candidate_human_review items",
        "approved_only": "Use only candidates with approved_candidate=true and action approve/edit.",
        "excluded": "Rejected, pending, merged, same duplicate_group_key, same object_name, self-pairs, and PN68 sources are excluded.",
        "evidence": "A candidate relation requires shared source_server_note_id, shared evidence_chunk_id, or same page with type compatibility.",
        "confidence": "Confidence is deterministic and bounded by evidence proximity; it is not an LLM judgment.",
        "persistence": "Dry-run only; should_save=false and relation_rows_written=false for every relation candidate.",
        "mechanism": "suggests_mechanism is only a relation_type label; no mechanism candidate is generated.",
    }


def _relation_dry_run_safety_flags() -> dict[str, Any]:
    return review_pipeline_safety_flags(
        db_write_performed=False,
        relation_candidates_generated=False,
        relation_rows_written=False,
        relation_generated=False,
        mechanism_generated=False,
        object_registry_write_performed=False,
        llm_called=False,
        zotero_write_performed=False,
        vector_write_performed=False,
        generation_performed=False,
    )


def _phase7g_relation_source_candidate(item: Mapping[str, Any]) -> dict[str, Any]:
    candidate_id = str(item.get("candidate_temp_id") or "")
    source_server_note_ids = _clean_string_list(item.get("source_server_note_ids") or [])
    evidence_chunk_ids = _clean_int_list(item.get("evidence_chunk_ids") or [])
    page_labels = _clean_string_list(item.get("page_labels") or [])
    action = str(item.get("action") or "pending")
    object_name = str(item.get("final_object_name") or "").strip()
    object_type = str(item.get("final_object_type") or "").strip()
    return {
        "candidate_id": candidate_id,
        "candidate_temp_id": candidate_id,
        "source_draft_item_id": item.get("source_draft_item_id"),
        "source_human_review_item_id": item.get("review_item_id"),
        "object_name": object_name,
        "object_type": object_type,
        "source_server_note_ids": source_server_note_ids,
        "source_labels": _clean_string_list(item.get("source_labels") or []),
        "evidence_chunk_ids": evidence_chunk_ids,
        "page_labels": page_labels,
        "duplicate_group_key": str(item.get("duplicate_group_key") or ""),
        "current_human_action": action,
        "approved_candidate": bool(item.get("approved_candidate")),
        "pn68_source": PRODUCTION_REVIEW_SECTION84_PN68_SERVER_NOTE_ID in source_server_note_ids,
    }


def _phase7g_excluded_candidate_summary(candidate: Mapping[str, Any], *, reason: str) -> dict[str, Any]:
    return {
        "candidate_id": candidate.get("candidate_id"),
        "object_name": candidate.get("object_name"),
        "object_type": candidate.get("object_type"),
        "reason": reason,
        "source_server_note_ids": candidate.get("source_server_note_ids") or [],
        "pn68_source": bool(candidate.get("pn68_source")),
    }


def _phase7g_relation_candidates_for_approved(
    approved_candidates: list[Mapping[str, Any]],
    *,
    source_object_candidate_human_review_id: str,
) -> list[dict[str, Any]]:
    relations: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    ordered = sorted(
        approved_candidates,
        key=lambda candidate: (
            OBJECT_CANDIDATE_DRY_RUN_TYPE_ORDER.index(str(candidate.get("object_type")))
            if str(candidate.get("object_type")) in OBJECT_CANDIDATE_DRY_RUN_TYPES
            else 999,
            str(candidate.get("object_name") or "").lower(),
            str(candidate.get("candidate_id") or ""),
        ),
    )
    for index, left in enumerate(ordered):
        for right in ordered[index + 1 :]:
            relation = _phase7g_relation_candidate_for_pair(
                left,
                right,
                source_object_candidate_human_review_id=source_object_candidate_human_review_id,
            )
            if not relation:
                continue
            key = (
                str(relation["relation_type"]),
                str(relation["subject_candidate_id"]),
                str(relation["object_candidate_id"]),
            )
            if key in seen:
                continue
            seen.add(key)
            relations.append(relation)
    return sorted(
        relations,
        key=lambda relation: (
            RELATION_CANDIDATE_DRY_RUN_TYPE_ORDER.index(str(relation.get("relation_type")))
            if str(relation.get("relation_type")) in RELATION_CANDIDATE_DRY_RUN_TYPES
            else 999,
            str(relation.get("subject_object_name") or ""),
            str(relation.get("object_object_name") or ""),
            str(relation.get("relation_temp_id") or ""),
        ),
    )


def _phase7g_relation_candidate_for_pair(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
    *,
    source_object_candidate_human_review_id: str,
) -> dict[str, Any] | None:
    left_id = str(left.get("candidate_id") or "")
    right_id = str(right.get("candidate_id") or "")
    if not left_id or not right_id or left_id == right_id:
        return None
    if str(left.get("duplicate_group_key") or "") and left.get("duplicate_group_key") == right.get("duplicate_group_key"):
        return None
    if str(left.get("object_name") or "").strip().lower() == str(right.get("object_name") or "").strip().lower():
        return None
    if left.get("pn68_source") or right.get("pn68_source"):
        return None

    shared_sources = sorted(set(left.get("source_server_note_ids") or []) & set(right.get("source_server_note_ids") or []))
    shared_chunks = sorted(set(left.get("evidence_chunk_ids") or []) & set(right.get("evidence_chunk_ids") or []))
    shared_pages = sorted(set(left.get("page_labels") or []) & set(right.get("page_labels") or []), key=_phase7g_page_sort_key)
    relation_type, subject, obj = _phase7g_relation_type_and_direction(left, right)
    if not relation_type:
        if not shared_sources:
            return None
        relation_type, subject, obj = "related_to", left, right
    compatible = relation_type != "related_to"
    if not shared_sources and not shared_chunks and not (shared_pages and compatible):
        return None

    evidence_basis = (
        "shared_source_server_note_id"
        if shared_sources
        else "shared_evidence_chunk_id"
        if shared_chunks
        else "same_page_type_compatibility"
    )
    confidence = _phase7g_relation_confidence(
        relation_type=relation_type,
        shared_sources=shared_sources,
        shared_chunks=shared_chunks,
        shared_pages=shared_pages,
        compatible=compatible,
    )
    source_server_note_ids = shared_sources or sorted(
        set(left.get("source_server_note_ids") or []) | set(right.get("source_server_note_ids") or [])
    )
    evidence_chunk_ids = shared_chunks or sorted(
        set(left.get("evidence_chunk_ids") or []) | set(right.get("evidence_chunk_ids") or [])
    )
    page_labels = shared_pages or sorted(
        set(left.get("page_labels") or []) | set(right.get("page_labels") or []),
        key=_phase7g_page_sort_key,
    )
    seed = "|".join(
        [
            source_object_candidate_human_review_id,
            relation_type,
            str(subject.get("candidate_id") or ""),
            str(obj.get("candidate_id") or ""),
            ",".join(source_server_note_ids),
        ]
    )
    return {
        "relation_temp_id": f"reldry_{hashlib.sha1(seed.encode('utf-8')).hexdigest()[:16]}",
        "relation_type": relation_type,
        "subject_candidate_id": subject.get("candidate_id"),
        "object_candidate_id": obj.get("candidate_id"),
        "subject_object_name": subject.get("object_name"),
        "object_object_name": obj.get("object_name"),
        "subject_object_type": subject.get("object_type"),
        "object_object_type": obj.get("object_type"),
        "source_object_candidate_ids": [subject.get("candidate_id"), obj.get("candidate_id")],
        "source_server_note_ids": source_server_note_ids,
        "evidence_chunk_ids": evidence_chunk_ids,
        "page_labels": page_labels,
        "confidence": confidence,
        "evidence_basis": evidence_basis,
        "rationale": _phase7g_relation_rationale(
            relation_type=relation_type,
            subject=subject,
            obj=obj,
            evidence_basis=evidence_basis,
            source_count=len(source_server_note_ids),
            chunk_count=len(evidence_chunk_ids),
            page_labels=page_labels,
        ),
        "review_status": "dry_run_only",
        "should_save": False,
        "db_write_performed": False,
        "relation_rows_written": False,
        "mechanism_generated": False,
        "llm_called": False,
        "zotero_write_performed": False,
        "vector_write_performed": False,
    }


def _phase7g_relation_type_and_direction(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
) -> tuple[str | None, Mapping[str, Any], Mapping[str, Any]]:
    left_type = str(left.get("object_type") or "")
    right_type = str(right.get("object_type") or "")
    method_like = {"method", "algorithm", "model", "experiment_candidate"}
    concept_like = {"concept", "theorem_or_principle"}
    if left_type == "research_problem" and right_type in method_like | concept_like:
        return "addresses_problem", right, left
    if right_type == "research_problem" and left_type in method_like | concept_like:
        return "addresses_problem", left, right
    if left_type == "metric" and right_type in method_like:
        return "evaluates_with_metric", right, left
    if right_type == "metric" and left_type in method_like:
        return "evaluates_with_metric", left, right
    if left_type == "dataset" and right_type in method_like:
        return "evaluated_on_dataset", right, left
    if right_type == "dataset" and left_type in method_like:
        return "evaluated_on_dataset", left, right
    if left_type == "mechanism_candidate" and right_type in method_like | concept_like:
        return "suggests_mechanism", left, right
    if right_type == "mechanism_candidate" and left_type in method_like | concept_like:
        return "suggests_mechanism", right, left
    if left_type in {"algorithm", "model"} and right_type == "method":
        return "uses_method", left, right
    if right_type in {"algorithm", "model"} and left_type == "method":
        return "uses_method", right, left
    if left_type in method_like and right_type in concept_like:
        return "supports", left, right
    if right_type in method_like and left_type in concept_like:
        return "supports", right, left
    if left_type in concept_like and right_type in concept_like:
        return "related_to", left, right
    if left_type == "metric" or right_type == "metric":
        return "related_to", left, right
    return None, left, right


def _phase7g_relation_confidence(
    *,
    relation_type: str,
    shared_sources: list[str],
    shared_chunks: list[int],
    shared_pages: list[str],
    compatible: bool,
) -> float:
    if shared_sources and compatible:
        value = 0.72
    elif shared_chunks and compatible:
        value = 0.66
    elif shared_sources:
        value = 0.58
    elif shared_chunks:
        value = 0.56
    elif shared_pages and compatible:
        value = 0.54
    else:
        value = 0.5
    if relation_type == "related_to":
        value = min(value, 0.58)
    return round(value, 2)


def _phase7g_relation_rationale(
    *,
    relation_type: str,
    subject: Mapping[str, Any],
    obj: Mapping[str, Any],
    evidence_basis: str,
    source_count: int,
    chunk_count: int,
    page_labels: list[str],
) -> str:
    pages = ", ".join(page_labels[:4]) or "n/a"
    return (
        f"Dry-run {relation_type} candidate from approved Phase7F objects "
        f"'{subject.get('object_name')}' and '{obj.get('object_name')}'. "
        f"Evidence basis: {evidence_basis}; source notes={source_count}; chunks={chunk_count}; pages={pages}. "
        "No relation row, mechanism, object registry entry, Zotero write, vector write, or LLM call is performed."
    )


def _phase7g_page_sort_key(value: Any) -> tuple[int, str]:
    text = str(value or "")
    match = re.search(r"\d+", text)
    return (int(match.group(0)) if match else 999999, text)


def _clean_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _clean_int_list(value: Any) -> list[int]:
    if not isinstance(value, list):
        return []
    result: list[int] = []
    for item in value:
        parsed = _int_or_none(item)
        if parsed is not None and parsed not in result:
            result.append(parsed)
    return result


def build_phase7d_object_candidate_prompt_preview(
    *,
    package_summary: Mapping[str, Any],
    validator_contract: Mapping[str, Any],
) -> str:
    return "\n".join(
        [
            "# NOTEBOOK_AI Phase7D object candidate dry-run prompt preview",
            "",
            "This is a future manual/controlled-generation prompt preview. NOTEBOOK_AI must not call an LLM in Phase7D.",
            "",
            "## Task boundary",
            "Use the saved note_classification_review to propose object candidate JSON only.",
            "Do not save object_candidates. Do not generate relations. Do not generate mechanisms.",
            "",
            "## Package summary",
            json.dumps(package_summary, ensure_ascii=False, indent=2, sort_keys=True),
            "",
            "## Allowed object types",
            json.dumps(list(OBJECT_CANDIDATE_DRY_RUN_TYPE_ORDER), ensure_ascii=False, indent=2),
            "",
            "## Quarantine policy",
            "PN68 and any unclear / needs_manual_review item must stay quarantined unless a future manual override is explicitly approved.",
            "",
            "## Validator contract",
            json.dumps(validator_contract, ensure_ascii=False, indent=2, sort_keys=True),
        ]
    )


def build_tri_source_object_package_preview(
    *,
    research_db_path: str | Path = DEFAULT_DB_PATH,
    document_id: int,
    chapter_id: int,
) -> dict[str, Any]:
    correction_package = build_chapter_note_correction_prompt_package(
        research_db_path=research_db_path,
        document_id=document_id,
        chapter_id=chapter_id,
    )
    correction_saved = bool(
        load_saved_note_correction_review(
            research_db_path=research_db_path,
            document_id=document_id,
            chapter_id=chapter_id,
        )
    )
    classification_saved = _saved_classification_review_exists(
        research_db_path=research_db_path,
        document_id=document_id,
        chapter_id=chapter_id,
    )
    notes_summary = correction_package.get("notes_summary") or {}
    chapter_context = correction_package.get("chapter_context") or {}
    flags = review_pipeline_safety_flags()
    ready = correction_saved and classification_saved
    object_dry_run: dict[str, Any] | None = None
    if classification_saved:
        object_dry_run = build_chapter_object_candidate_dry_run_package(
            research_db_path=research_db_path,
            document_id=document_id,
            chapter_id=chapter_id,
        )
    return {
        "status": "planned" if ready else "not_ready",
        "ready": ready,
        "mode": "r3_tri_source_object_package_preview",
        "document_id": document_id,
        "chapter_id": chapter_id,
        "reason": None if ready else "note_correction_and_classification_reviews_required",
        "source_modes": [
            "note_anchored_object",
            "highlight_anchored_object",
            "chapter_global_object",
        ],
        "source_statuses": {
            "note_anchored_object": {
                "status": "planned_not_generated" if ready else "blocked",
                "reason": None if ready else "waiting_note_correction_and_classification",
                "candidate_basis": "corrected_and_classified_user_notes",
                "user_note_count": int(notes_summary.get("correction_candidate_count") or 0),
            },
            "highlight_anchored_object": {
                "status": "planned_not_implemented",
                "reason": "highlight_anchored_source_planned_for_evidence_only_annotations",
                "evidence_only_count": int(notes_summary.get("supporting_evidence_count") or 0),
            },
            "chapter_global_object": {
                "status": "planned_not_implemented",
                "reason": "chapter_global_source_planned_for_full_chapter_chunks",
                "chunk_count": int(chapter_context.get("chunk_count") or 0),
            },
        },
        "preconditions": {
            "note_correction_review_saved": correction_saved,
            "note_classification_review_saved": classification_saved,
            "object_review_required": True,
            "unified_object_review_before_merge": True,
        },
        "object_candidate_dry_run_summary": _phase7d_object_candidate_summary(object_dry_run),
        "relation_layer_preview": {
            "related_objects": "planned",
            "relation_candidates": "planned",
            "research_insight_card": "planned",
            "search_entry_terms": "planned",
        },
        "object_candidates": [],
        "object_candidates_generated": False,
        "relation_candidates_generated": False,
        "mechanism_generated": False,
        "safety_flags": flags,
        **flags,
    }


def _phase7d_object_candidate_summary(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    if not payload:
        return {
            "ready": False,
            "status": "blocked",
            "reason": "note_classification_review_not_saved",
            "candidate_count": 0,
            "quarantined_count": 0,
            "pn68_quarantined": False,
            "save_forbidden_until_phase7e_gate": True,
        }
    return {
        "ready": bool(payload.get("ready")),
        "status": payload.get("status"),
        "source_classification_review_id": payload.get("source_classification_review_id"),
        "source_item_count": payload.get("source_item_count"),
        "label_distribution": payload.get("label_distribution") or {},
        "candidate_count": payload.get("candidate_count"),
        "quarantined_count": payload.get("quarantined_count"),
        "pn68_quarantined": bool(payload.get("pn68_quarantined")),
        "validator_valid": bool((payload.get("validator_result") or {}).get("valid")),
        "object_candidate_save_status": payload.get("object_candidate_save_status"),
        "save_forbidden_until_phase7e_gate": bool(payload.get("save_forbidden_until_phase7e_gate", True)),
        "object_candidate_draft_review_status": payload.get("object_candidate_draft_review_status") or "not_saved",
        "object_candidate_draft_review_id": payload.get("object_candidate_draft_review_id"),
        "object_candidate_draft_saved_count": payload.get("object_candidate_draft_saved_count") or 0,
        "saved_draft_review": payload.get("saved_draft_review"),
        "object_candidates_generated": bool(payload.get("object_candidates_generated")),
        "relation_generated": bool(payload.get("relation_generated")),
        "mechanism_generated": bool(payload.get("mechanism_generated")),
    }


def load_saved_note_correction_review(
    *,
    research_db_path: str | Path = DEFAULT_DB_PATH,
    document_id: int,
    chapter_id: int,
) -> dict[str, Any] | None:
    db_path = Path(research_db_path)
    conn = sqlite3.connect(f"file:{db_path.resolve(strict=False).as_posix()}?mode=ro", uri=True)
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
    conn = sqlite3.connect(f"file:{db_path.resolve(strict=False).as_posix()}?mode=ro", uri=True)
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
        (item for item in saved_items if item.get("zotero_annotation_key") == "PN68YPTT"),
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


def classification_taxonomy() -> list[dict[str, str]]:
    return [
        {"label": "memory_note", "description": "读者希望保留的概念、事实、方法定义或章节记忆。"},
        {"label": "connection_note", "description": "跨概念、跨章节、跨方法或与既有知识的连接。"},
        {"label": "mechanism_note", "description": "指向因果链、工作机理、失败原因或机制假设的笔记。"},
        {"label": "research_idea_note", "description": "可转化为实验、问题、改进方向或研究计划的想法。"},
        {"label": "unclear", "description": "语义不足、证据弱或暂不能稳定归类的笔记。"},
        {"label": "needs_manual_review", "description": "存在对齐、证据、身份或解释风险，需要人工单独复核。"},
    ]


def classification_taxonomy_audit() -> dict[str, Any]:
    return {
        "labels": list(NOTE_CLASSIFICATION_LABEL_ORDER),
        "required_fields": [
            "note_id",
            "server_note_id",
            "client_note_id",
            "zotero_annotation_key",
            "source_section_id",
            "page",
            "original_note_text",
            "corrected_note_text",
            "selected_text",
            "matched_chunk_id",
            "correction_status",
            "issue_type",
            "evidence_support",
            "reviewer_warning",
        ],
        "special_states": {
            "evidence_only": "not a classification label; remains supporting evidence and must not enter 67 user-note candidates",
            "unclear": "allowed label for weak or underspecified classification",
            "needs_manual_review": "allowed label for alignment, identity, or evidence risk",
        },
        "pn68_handling_policy": {
            "warning_preserved_required": True,
            "recommended_handling": "manual_review_or_unclear_classification",
            "high_confidence_mechanism_note_requires_warning_handled": True,
            "classification_can_proceed_without_resolving_matched_chunk_id": True,
        },
        "generation_boundary": {
            "llm_called": False,
            "object_generation_allowed": False,
            "relation_generation_allowed": False,
            "mechanism_generation_allowed": False,
            "db_write_allowed": False,
        },
    }


def note_classification_output_schema() -> dict[str, Any]:
    labels = "|".join(NOTE_CLASSIFICATION_LABEL_ORDER)
    return {
        "note_classification_review": {
            "review_type": "note_classification_review",
            "document_id": "number",
            "chapter_id": "number",
            "summary": {
                "total_items": "number",
                "primary_type_counts": "object",
                "mechanism_prompt_eligible_count": "number",
            },
            "items": "array[note_classification_review_result] with exactly the classification_candidate_count",
        },
        "note_classification_review_result": {
            "note_id": "string",
            "server_note_id": "string|null",
            "client_note_id": "string|null",
            "zotero_annotation_key": "string",
            "original_note_text": "string optional; if returned, it must exactly match the input original_note_text",
            "primary_type": labels,
            "secondary_types": f"array[{labels}]",
            "confidence": "number_between_0_and_1",
            "classification_rationale": "string",
            "user_tag_agreement": "|".join(sorted(USER_TAG_AGREEMENTS)),
            "mechanism_prompt_eligible": "boolean",
            "reason_not_mechanism": "string|null",
            "pn68_warning_handled": "boolean optional; required true only if PN68 is classified as high-confidence mechanism_note",
        },
    }


def phase7b_manual_classification_expected_schema() -> dict[str, Any]:
    return {
        "document_id": 10,
        "chapter_id": 69,
        "source_package_hash": "string recommended; warning if omitted",
        "items": [
            {
                "server_note_id": "string; must match one Phase7A note",
                "note_type": "|".join(NOTE_CLASSIFICATION_LABEL_ORDER),
                "confidence": "|".join(NOTE_CLASSIFICATION_MANUAL_CONFIDENCE_ORDER),
                "rationale": "string; required for mechanism_note, research_idea_note, needs_manual_review",
                "preserve_original_note_text": True,
                "warnings": "array[string]; PN68 must include alignment_uncertain or unmatched",
            }
        ],
        "forbidden_fields": sorted(_manual_forbidden_field_names()),
        "no_write": True,
    }


def build_note_classification_copy_ready_prompt(package: Mapping[str, Any]) -> str:
    return build_phase7a_classification_prompt_preview(package)


def build_phase7a_classification_prompt_preview(package: Mapping[str, Any]) -> str:
    package_for_prompt = {
        key: value
        for key, value in package.items()
        if key not in {"copy_ready_prompt", "classification_candidates"}
    }
    note_summaries = package.get("note_summaries") or _phase7a_note_summaries(
        list(package.get("classification_candidates") or package.get("corrected_notes") or [])
    )
    pn68 = package.get("pn68") or _phase7a_pn68_status(
        list(package.get("classification_candidates") or package.get("corrected_notes") or []),
        package.get("pn68_status") or {},
    )
    return "\n".join(
        [
            "# NOTEBOOK_AI Phase7A note classification dry-run prompt preview",
            "",
            "This prompt preview is for manual ChatGPT or a future controlled-generation gate. Do not call OpenAI from NOTEBOOK_AI in Phase7A.",
            "",
            "## Task instruction",
            "Classify exactly the 67 reviewed user notes from the merged saved note_correction_review. Return only note_classification_review JSON.",
            "Preserve every original_note_text exactly as input if you echo it. Do not overwrite original note text.",
            "",
            "## Allowed labels",
            ", ".join(NOTE_CLASSIFICATION_LABEL_ORDER),
            "",
            "## Special-state rules",
            "- evidence_only is not a classification label; evidence-only annotations stay out of the 67 user-note candidates.",
            "- Use unclear when the note is semantically weak or cannot be assigned safely.",
            "- Use needs_manual_review when identity, alignment, page, bbox, or evidence support is risky.",
            "- PN68 must keep its warning. Do not treat it as fully aligned evidence.",
            "- PN68 cannot be high-confidence mechanism_note unless pn68_warning_handled=true and the rationale explicitly addresses the warning.",
            "",
            "## Input schema",
            json.dumps(classification_taxonomy_audit()["required_fields"], ensure_ascii=False, indent=2),
            "",
            "## Output JSON schema",
            json.dumps(note_classification_output_schema(), ensure_ascii=False, indent=2),
            "",
            "## Validation rules",
            json.dumps(
                build_phase7a_classification_validator_contract(package=package, pn68=pn68).get("rules"),
                ensure_ascii=False,
                indent=2,
            ),
            "",
            "## PN68 warning",
            json.dumps(pn68, ensure_ascii=False, indent=2),
            "",
            "## 67 note summaries",
            json.dumps(note_summaries, ensure_ascii=False, indent=2),
            "",
            "## Dry-run package metadata",
            json.dumps(package_for_prompt, ensure_ascii=False, indent=2),
        ]
    )


def build_phase7a_classification_validator_contract(
    *,
    package: Mapping[str, Any],
    section_distribution: Mapping[str, int] | None = None,
    pn68: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    candidates = list(package.get("classification_candidates") or package.get("corrected_notes") or [])
    expected_server_note_ids = [
        str(candidate.get("server_note_id") or "").strip()
        for candidate in candidates
        if str(candidate.get("server_note_id") or "").strip()
    ]
    pn68_status = pn68 or _phase7a_pn68_status(candidates, package.get("pn68_status") or {})
    flags = review_pipeline_safety_flags()
    return {
        "schema_version": "r3_phase7a_classification_validator_contract_v1",
        "document_id": package.get("document_id"),
        "chapter_id": package.get("chapter_id"),
        "source": "merged_saved_note_correction_review",
        "expected_item_count": int(package.get("item_count") or len(candidates)),
        "expected_unique_server_note_ids": int(package.get("unique_server_note_ids") or len(set(expected_server_note_ids))),
        "expected_server_note_ids": expected_server_note_ids,
        "section_distribution": dict(section_distribution or _classification_section_distribution(candidates)),
        "allowed_labels": list(NOTE_CLASSIFICATION_LABEL_ORDER),
        "pn68": pn68_status,
        "rules": [
            "review_type must be note_classification_review",
            "document_id and chapter_id must match the dry-run package",
            "items length must equal expected_item_count",
            "every server_note_id must match one expected candidate",
            "no duplicate server_note_id/client_note_id candidate identity",
            "no missing expected candidate identity",
            "primary_type and secondary_types must use allowed_labels only",
            "PN68 cannot be high-confidence mechanism_note unless warning is explicitly handled",
            "original_note_text must not be overwritten when returned",
            "classification validation must not generate object/relation/mechanism candidates",
            "classification validation must be no-write unless an explicit future save gate is enabled",
        ],
        "no_write_boundary": {
            "db_write_allowed": False,
            "zotero_write_allowed": False,
            "vector_write_allowed": False,
            "llm_allowed": False,
            "object_relation_mechanism_generation_allowed": False,
        },
        "safety_flags": flags,
        **flags,
    }


def _classification_section_distribution(notes: list[Mapping[str, Any]]) -> dict[str, int]:
    counts = Counter(str(note.get("source_section_id") or "unknown") for note in notes)
    ordered = {
        section_id: int(counts.get(section_id, 0))
        for section_id in MERGED_NOTE_CORRECTION_SECTION_ORDER
        if counts.get(section_id, 0)
    }
    for section_id, count in sorted(counts.items()):
        if section_id not in ordered:
            ordered[section_id] = int(count)
    return ordered


def _phase7a_pn68_status(
    notes: list[Mapping[str, Any]],
    saved_pn68_status: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    pn68_notes = [
        note
        for note in notes
        if note.get("zotero_annotation_key") == PRODUCTION_REVIEW_SECTION84_PN68_ZOTERO_KEY
        or note.get("server_note_id") == PRODUCTION_REVIEW_SECTION84_PN68_SERVER_NOTE_ID
    ]
    note = pn68_notes[0] if pn68_notes else {}
    warning = str(note.get("reviewer_warning") or (saved_pn68_status or {}).get("reviewer_warning") or "").strip()
    return {
        "included": bool(pn68_notes),
        "included_count": len(pn68_notes),
        "included_once": len(pn68_notes) == 1,
        "warning_preserved": bool(warning),
        "recommended_handling": "manual_review_or_unclear_classification",
        "server_note_id": note.get("server_note_id") or (saved_pn68_status or {}).get("server_note_id"),
        "client_note_id": note.get("client_note_id"),
        "zotero_annotation_key": note.get("zotero_annotation_key") or (saved_pn68_status or {}).get("zotero_annotation_key"),
        "source_section_id": note.get("source_section_id"),
        "page": note.get("page"),
        "matched_chunk_id": note.get("matched_chunk_id"),
        "correction_status": note.get("correction_status") or (saved_pn68_status or {}).get("correction_status"),
        "issue_type": note.get("issue_type") or (saved_pn68_status or {}).get("issue_type"),
        "evidence_support": note.get("evidence_support") or (saved_pn68_status or {}).get("evidence_support"),
        "reviewer_warning": warning,
        "human_action": note.get("human_action") or (saved_pn68_status or {}).get("human_action"),
        "writeback_intent": note.get("writeback_intent") or (saved_pn68_status or {}).get("writeback_intent"),
        "can_proceed_without_resolving_matched_chunk_id": True,
    }


def _phase7a_note_summaries(notes: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for index, note in enumerate(notes, start=1):
        original_note_text = str(note.get("original_note_text") or "")
        corrected_note_text = str(note.get("corrected_note_text") or "")
        selected_text = str(note.get("selected_text") or note.get("selected_text_preview") or "")
        is_pn68 = (
            note.get("zotero_annotation_key") == PRODUCTION_REVIEW_SECTION84_PN68_ZOTERO_KEY
            or note.get("server_note_id") == PRODUCTION_REVIEW_SECTION84_PN68_SERVER_NOTE_ID
        )
        summaries.append(
            {
                "index": index,
                "source_section_id": note.get("source_section_id"),
                "note_id": note.get("note_id"),
                "server_note_id": note.get("server_note_id"),
                "client_note_id": note.get("client_note_id"),
                "zotero_annotation_key": note.get("zotero_annotation_key"),
                "page": note.get("page"),
                "matched_chunk_id": note.get("matched_chunk_id"),
                "original_note_text": original_note_text,
                "original_note_text_excerpt": _excerpt(original_note_text, 280),
                "corrected_note_text": corrected_note_text,
                "corrected_note_text_excerpt": _excerpt(corrected_note_text, 280),
                "selected_text_excerpt": _excerpt(selected_text, 280),
                "correction_status": note.get("correction_status"),
                "issue_type": note.get("issue_type"),
                "evidence_support": note.get("evidence_support"),
                "reviewer_warning": note.get("reviewer_warning"),
                "human_action": note.get("human_action"),
                "writeback_intent": note.get("writeback_intent") or "none",
                "pn68_warning": is_pn68,
                "recommended_label_if_uncertain": "needs_manual_review" if is_pn68 else "unclear",
            }
        )
    return summaries


def _excerpt(value: Any, limit: int = 240) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(limit - 1, 0)].rstrip() + "..."


def _hash_json_for_contract(value: Mapping[str, Any]) -> str:
    payload = {
        key: child
        for key, child in value.items()
        if key != "copy_ready_prompt"
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def _pn68_warning_handled(raw_item: Mapping[str, Any]) -> bool:
    if raw_item.get("pn68_warning_handled") is True or raw_item.get("warning_handled") is True:
        return True
    combined = " ".join(
        str(raw_item.get(key) or "")
        for key in ["classification_rationale", "reason_not_mechanism", "reviewer_warning"]
    ).lower()
    return any(
        token in combined
        for token in [
            "pn68",
            "warning",
            "alignment_uncertain",
            "manual_review",
            "needs_manual_review",
            "unclear",
        ]
    )


def _parse_manual_classification_payload(value: str | Mapping[str, Any] | list[Any], errors: list[str]) -> Any:
    parsed: Any
    if isinstance(value, str):
        text = value.strip()
        if not text:
            errors.append("manual classification JSON is required")
            return None
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            errors.append(f"manual classification JSON is invalid: {exc.msg}")
            return None
    else:
        parsed = value
    if isinstance(parsed, Mapping):
        if "classification_json" in parsed:
            return _parse_manual_classification_payload(parsed.get("classification_json"), errors)
        if "json_text" in parsed:
            return _parse_manual_classification_payload(str(parsed.get("json_text") or ""), errors)
        if "review_json" in parsed:
            return _parse_manual_classification_payload(parsed.get("review_json"), errors)
    return parsed


def _manual_warning_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if value is None:
        return []
    text = str(value).strip()
    return [text] if text else []


def _manual_forbidden_field_names() -> set[str]:
    return {
        *FORBIDDEN_REVIEW_KEYS,
        "object_candidates",
        "object_results",
        "objects",
        "relation_candidates",
        "relation_results",
        "relations",
        "mechanism_candidates",
        "mechanism_review_candidate",
        "mechanism_results",
        "mechanisms",
        "tri_source_object_package",
        "writeback_intent",
        "writeback_target",
        "zotero_writeback",
        "zotero_writeback_planned",
        "vector_write",
        "classification_save",
    }


def _manual_forbidden_keys(value: Any) -> set[str]:
    found: set[str] = set()
    forbidden_names = _manual_forbidden_field_names()
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key) in forbidden_names:
                found.add(str(key))
            found.update(_manual_forbidden_keys(child))
    elif isinstance(value, list):
        for child in value:
            found.update(_manual_forbidden_keys(child))
    return found


def build_note_classification_copy_ready_prompt_legacy(package: Mapping[str, Any]) -> str:
    package_for_prompt = {
        key: value
        for key, value in package.items()
        if key != "copy_ready_prompt"
    }
    return "\n".join(
        [
            "# NOTEBOOK_AI 笔记分类审核输入提示词",
            "",
            "## 审核任务说明",
            "请只执行 note_classification_review。输入来自已保存的 note_correction_review，不是对象候选。",
            "请根据 corrected_notes、原始笔记、证据支持、note anchors 和章节上下文为每条笔记分类。",
            "",
            "## 禁止事项",
            "禁止生成 object_candidates、relation_candidates、mechanism_review_candidate、机制或 insight。",
            "禁止写入 NOTEBOOK_AI、Zotero、PDF、tags、数据库或 vector store。",
            "",
            "## 输出 JSON schema",
            json.dumps(note_classification_output_schema(), ensure_ascii=False, indent=2),
            "",
            "## 完整 note_classification package JSON",
            json.dumps(package_for_prompt, ensure_ascii=False, indent=2),
        ]
    )


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


def _blocked_classification_package(
    *,
    document_id: int,
    chapter_id: int,
    correction_package: Mapping[str, Any],
    reason: str,
    note_correction_review_saved: bool = False,
    needs_followup_count: int = 0,
) -> dict[str, Any]:
    flags = review_pipeline_safety_flags()
    summary = correction_package.get("notes_summary") or {}
    return {
        "status": "blocked",
        "ready": False,
        "mode": "r3_note_classification_package_dry_run",
        "document_id": document_id,
        "chapter_id": chapter_id,
        "reason": reason,
        "note_correction_review_saved": note_correction_review_saved,
        "needs_followup_count": needs_followup_count,
        "candidate_count": int(summary.get("correction_candidate_count") or 0),
        "supporting_evidence_count": int(summary.get("supporting_evidence_count") or 0),
        "classification_taxonomy": classification_taxonomy(),
        "output_schema": note_classification_output_schema(),
        "review_pipeline": {
            "current_gate": "note_classification_review",
            "blocked_by": "note_correction_review",
            "gate_status": (
                "blocked needs_followup_items"
                if needs_followup_count
                else f"blocked {reason}"
            ),
        },
        "safety_flags": flags,
        **flags,
    }


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


def _connect_rw_existing(db_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{db_path.resolve(strict=False).as_posix()}?mode=rw", uri=True)


def _connect_ro_existing(db_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{db_path.resolve(strict=False).as_posix()}?mode=ro", uri=True)


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


def _review_save_block_reason(readiness: Mapping[str, Any]) -> str:
    blockers = list(readiness.get("current_blockers") or [])
    if "production_db_write_disabled" in blockers:
        return "production_db_write_disabled"
    if "review_schema_missing" in blockers:
        return "review_schema_missing"
    if "review_db_unavailable" in blockers:
        return "review_db_unavailable"
    return "production_review_write_not_allowed"


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


def _primary_note_identity_keys(item: Mapping[str, Any] | None) -> list[str]:
    if not item:
        return []
    keys: list[str] = []
    for value in [item.get("server_note_id"), item.get("client_note_id")]:
        text = str(value or "").strip()
        if text and text not in keys:
            keys.append(text)
    return keys


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


def _saved_classification_review_exists(
    *,
    research_db_path: str | Path,
    document_id: int,
    chapter_id: int,
) -> bool:
    conn = sqlite3.connect(Path(research_db_path))
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


def _object_candidate_payload_requests_forbidden_side_effects(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key in [
            "llm_called",
            "external_llm_called",
            "zotero_write_performed",
            "zotero_db_write_performed",
            "vector_write_performed",
            "vector_store_write_performed",
            "object_candidates_generated",
            "approved_objects_created",
            "relation_generated",
            "mechanism_generated",
            "generation_performed",
            "approved",
        ]:
            if _truthy_true(value.get(key)):
                return True
        return any(_object_candidate_payload_requests_forbidden_side_effects(item) for item in value.values())
    if isinstance(value, list):
        return any(_object_candidate_payload_requests_forbidden_side_effects(item) for item in value)
    return False


def _truthy_true(value: Any) -> bool:
    if value is True:
        return True
    if isinstance(value, str) and value.strip().lower() in {"true", "1", "yes"}:
        return True
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value == 1:
        return True
    return False


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


def _corrected_notes_for_classification(
    correction_package: Mapping[str, Any],
    saved_review: Mapping[str, Any],
) -> list[dict[str, Any]]:
    review_items = {
        _item_key(item): item
        for item in saved_review.get("normalized_items", [])
        if _item_key(item)
    }
    human_items = {
        _item_key(item): item
        for item in saved_review.get("review_items", [])
        if _item_key(item)
    }
    corrected_notes = []
    for candidate in correction_package.get("correction_candidates") or []:
        review = review_items.get(_item_key(candidate)) or {}
        human = human_items.get(_item_key(candidate)) or {}
        original_note_text = candidate.get("note_text") or ""
        suggested_revision = review.get("suggested_revision")
        corrected_note_text = str(human.get("final_note_text") or suggested_revision or "").strip() or original_note_text
        corrected_notes.append(
            {
                "note_id": candidate.get("note_id"),
                "server_note_id": candidate.get("server_note_id"),
                "client_note_id": candidate.get("client_note_id"),
                "zotero_annotation_key": candidate.get("zotero_annotation_key"),
                "page": candidate.get("page"),
                "source_section_id": human.get("source_section_id"),
                "source_review_id": human.get("source_review_id"),
                "source_review_mode": human.get("source_review_mode"),
                "original_note_text": original_note_text,
                "corrected_note_text": corrected_note_text,
                "human_action": human.get("human_action"),
                "confirmed_by_user": bool(human.get("confirmed_by_user")),
                "writeback_intent": human.get("writeback_intent") or "none",
                "suggested_revision": suggested_revision,
                "correction_status": review.get("correction_status"),
                "issue_type": review.get("issue_type"),
                "correction_explanation": review.get("explanation"),
                "evidence_support": review.get("evidence_support"),
                "correction_confidence": review.get("confidence"),
                "reviewer_warning": review.get("reviewer_warning"),
                "selected_text": candidate.get("selected_text"),
                "selected_text_preview": candidate.get("selected_text_preview"),
                "matched_chunk_id": candidate.get("matched_chunk_id"),
                "matched_chunk_text": candidate.get("matched_chunk_text"),
                "chunk_evidence_text": candidate.get("chunk_evidence_text"),
                "note_anchor_id": candidate.get("note_anchor_id"),
                "anchor_method": candidate.get("anchor_method"),
                "warnings": candidate.get("warnings") or [],
            }
        )
    return corrected_notes


def _normalize_classification_item(
    raw_item: Any,
    *,
    index: int,
    candidate_index: Mapping[str, Mapping[str, Any]],
    errors: list[str],
    warnings: list[str],
) -> tuple[dict[str, Any] | None, str | None]:
    if not isinstance(raw_item, Mapping):
        errors.append(f"items[{index}] must be an object")
        return None, None
    note_id = _str_or_none(raw_item.get("note_id"))
    server_note_id = _str_or_none(raw_item.get("server_note_id"))
    client_note_id = _str_or_none(raw_item.get("client_note_id"))
    zotero_annotation_key = _str_or_none(raw_item.get("zotero_annotation_key"))
    if not any([note_id, server_note_id, client_note_id, zotero_annotation_key]):
        errors.append(f"items[{index}] must include note_id, server_note_id, client_note_id, or zotero_annotation_key")
    candidate = _match_classification_candidate(raw_item, candidate_index)
    matched_key = _classification_candidate_key(candidate) if candidate else None
    if candidate is None:
        errors.append(f"items[{index}] does not match any classification candidate")
        candidate = {}

    primary_type = _str_or_none(raw_item.get("primary_type"))
    secondary_types = raw_item.get("secondary_types") or []
    confidence = raw_item.get("confidence")
    user_tag_agreement = _str_or_none(raw_item.get("user_tag_agreement")) or "no_user_type_tag"
    mechanism_prompt_eligible = raw_item.get("mechanism_prompt_eligible")

    if primary_type not in NOTE_CLASSIFICATION_LABELS:
        errors.append(f"items[{index}].primary_type is invalid")
    if not isinstance(secondary_types, list) or any(str(item) not in NOTE_CLASSIFICATION_LABELS for item in secondary_types):
        errors.append(f"items[{index}].secondary_types contains invalid labels")
        secondary_types = []
    if not _is_confidence_score(confidence):
        errors.append(f"items[{index}].confidence must be a number from 0 to 1")
    if user_tag_agreement not in USER_TAG_AGREEMENTS:
        errors.append(f"items[{index}].user_tag_agreement is invalid")
    if not isinstance(mechanism_prompt_eligible, bool):
        errors.append(f"items[{index}].mechanism_prompt_eligible must be boolean")
    rationale = str(raw_item.get("classification_rationale") or "").strip()
    if not rationale:
        warnings.append(f"items[{index}].classification_rationale is empty")
    if "original_note_text" in raw_item:
        expected_original = str(candidate.get("original_note_text") or "")
        actual_original = str(raw_item.get("original_note_text") or "")
        if actual_original != expected_original:
            errors.append(f"items[{index}].original_note_text must not be overwritten")
    is_pn68 = (
        candidate.get("zotero_annotation_key") == PRODUCTION_REVIEW_SECTION84_PN68_ZOTERO_KEY
        or candidate.get("server_note_id") == PRODUCTION_REVIEW_SECTION84_PN68_SERVER_NOTE_ID
        or zotero_annotation_key == PRODUCTION_REVIEW_SECTION84_PN68_ZOTERO_KEY
        or server_note_id == PRODUCTION_REVIEW_SECTION84_PN68_SERVER_NOTE_ID
    )
    if (
        is_pn68
        and primary_type == "mechanism_note"
        and _is_confidence_score(confidence)
        and float(confidence) >= 0.8
        and not _pn68_warning_handled(raw_item)
    ):
        errors.append("PN68 cannot be high-confidence mechanism_note unless warning handled")

    normalized = {
        "note_id": note_id or _str_or_none(candidate.get("note_id")),
        "server_note_id": server_note_id or _str_or_none(candidate.get("server_note_id")),
        "client_note_id": client_note_id or _str_or_none(candidate.get("client_note_id")),
        "zotero_annotation_key": zotero_annotation_key or _str_or_none(candidate.get("zotero_annotation_key")),
        "page": raw_item.get("page") if raw_item.get("page") is not None else candidate.get("page"),
        "source_section_id": candidate.get("source_section_id"),
        "original_note_text": raw_item.get("original_note_text") if "original_note_text" in raw_item else None,
        "primary_type": primary_type,
        "secondary_types": [str(item) for item in secondary_types],
        "confidence": float(confidence) if _is_confidence_score(confidence) else confidence,
        "classification_rationale": rationale,
        "user_tag_agreement": user_tag_agreement,
        "mechanism_prompt_eligible": bool(mechanism_prompt_eligible) if isinstance(mechanism_prompt_eligible, bool) else mechanism_prompt_eligible,
        "reason_not_mechanism": raw_item.get("reason_not_mechanism"),
    }
    return normalized, matched_key


def _classification_stats(items: list[Mapping[str, Any]], *, expected_count: int) -> dict[str, Any]:
    primary_counts = Counter(str(item.get("primary_type") or "") for item in items)
    primary_counts.pop("", None)
    return {
        "expected_item_count": expected_count,
        "item_count": len(items),
        "primary_type_counts": dict(primary_counts),
        "mechanism_prompt_eligible_count": sum(1 for item in items if item.get("mechanism_prompt_eligible") is True),
    }


def _validate_classification_summary(summary: Any, stats: Mapping[str, Any], errors: list[str]) -> None:
    if not isinstance(summary, Mapping):
        errors.append("summary must be an object")
        return
    total = _int_or_none(summary.get("total_items"))
    if total != int(stats["item_count"]):
        errors.append("summary.total_items does not match items length")
    counts = summary.get("primary_type_counts")
    if not isinstance(counts, Mapping):
        errors.append("summary.primary_type_counts must be an object")
    else:
        keys = set(str(key) for key in counts.keys()) | set((stats.get("primary_type_counts") or {}).keys())
        for key in sorted(keys):
            expected = int((stats.get("primary_type_counts") or {}).get(key) or 0)
            actual = _int_or_none(counts.get(key)) or 0
            if actual != expected:
                errors.append(f"summary.primary_type_counts.{key} does not match items")
    eligible = _int_or_none(summary.get("mechanism_prompt_eligible_count"))
    if eligible != int(stats["mechanism_prompt_eligible_count"]):
        errors.append("summary.mechanism_prompt_eligible_count does not match items")


def _classification_candidate_index(candidates: list[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    index: dict[str, Mapping[str, Any]] = {}
    for candidate in candidates:
        for key in [
            candidate.get("note_id"),
            candidate.get("server_note_id"),
            candidate.get("client_note_id"),
            candidate.get("zotero_annotation_key"),
        ]:
            text = str(key or "").strip()
            if text:
                index[text] = candidate
    return index


def _match_classification_candidate(
    raw_item: Mapping[str, Any],
    candidate_index: Mapping[str, Mapping[str, Any]],
) -> Mapping[str, Any] | None:
    for key in [
        raw_item.get("note_id"),
        raw_item.get("server_note_id"),
        raw_item.get("client_note_id"),
        raw_item.get("zotero_annotation_key"),
    ]:
        text = str(key or "").strip()
        if text and text in candidate_index:
            return candidate_index[text]
    return None


def _classification_candidate_key(candidate: Mapping[str, Any] | None) -> str:
    if not candidate:
        return ""
    return _item_key(candidate)


def _item_key(item: Mapping[str, Any]) -> str:
    return str(
        item.get("server_note_id")
        or item.get("client_note_id")
        or item.get("note_id")
        or item.get("zotero_annotation_key")
        or ""
    ).strip()


def _forbidden_keys(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key) in FORBIDDEN_REVIEW_KEYS or str(key) in {
                "relation_candidates",
                "mechanism_candidates",
                "mechanism_review_candidate",
            }:
                found.add(str(key))
            found.update(_forbidden_keys(child))
    elif isinstance(value, list):
        for child in value:
            found.update(_forbidden_keys(child))
    return found


def _chapter_context_summary(package: Mapping[str, Any]) -> dict[str, Any]:
    context = package.get("chapter_context") or {}
    markdown = str(context.get("chapter_markdown") or context.get("chapter_md_text") or "")
    return {
        "context_scope": context.get("context_scope"),
        "has_chapter_markdown": bool(markdown.strip()),
        "chapter_markdown_chars": len(markdown),
        "chunk_count": context.get("chunk_count"),
        "source_path": context.get("source_path"),
        "md_source": context.get("md_source"),
    }


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
