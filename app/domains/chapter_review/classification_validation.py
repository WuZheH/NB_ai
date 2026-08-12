"""Chapter review classification validation responsibilities."""

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
    USER_TAG_AGREEMENTS,
)

from .classification import (
    build_chapter_note_classification_dry_run_package,
    build_chapter_note_classification_package,
)

from .classification_contracts import (
    _excerpt,
    _manual_forbidden_field_names,
    phase7b_manual_classification_expected_schema,
)

from .normalization import (
    _int_or_none,
    _is_confidence_score,
    _item_key,
    _parse_review_payload,
    _str_or_none,
)

from .safety import (
    review_pipeline_safety_flags,
)

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
