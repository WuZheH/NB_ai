"""Chapter review relations responsibilities."""

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
    RELATION_CANDIDATE_DRY_RUN_SCHEMA_VERSION,
    RELATION_CANDIDATE_VALIDATOR_CONTRACT_VERSION,
    RELATION_CANDIDATE_DRY_RUN_TYPE_ORDER,
    RELATION_CANDIDATE_DRY_RUN_TYPES,
)

from .loading import (
    load_saved_object_candidate_human_review,
)

from .normalization import (
    _int_or_none,
    _is_confidence_score,
)

from .safety import (
    review_pipeline_safety_flags,
)

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
            "# Search Phase7G relation candidate dry-run prompt preview",
            "",
            "This preview is for later manual review only. Search did not call an LLM in Phase7G.",
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
