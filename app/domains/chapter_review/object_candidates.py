"""Chapter review object candidates responsibilities."""

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
    OBJECT_CANDIDATE_DRY_RUN_QUARANTINE_LABELS,
)

from .loading import (
    _saved_classification_review_exists,
    load_saved_note_classification_review,
    load_saved_note_correction_review,
    load_saved_object_candidate_draft_review,
    load_saved_object_candidate_human_review,
)

from .normalization import (
    _int_or_none,
    _loads,
)

from .safety import (
    review_pipeline_safety_flags,
)

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


def build_phase7d_object_candidate_prompt_preview(
    *,
    package_summary: Mapping[str, Any],
    validator_contract: Mapping[str, Any],
) -> str:
    return "\n".join(
        [
            "# Search Phase7D object candidate dry-run prompt preview",
            "",
            "This is a future manual/controlled-generation prompt preview. Search must not call an LLM in Phase7D.",
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
