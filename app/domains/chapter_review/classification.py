"""Chapter review classification responsibilities."""

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

from .classification_contracts import (
    _classification_section_distribution,
    _phase7a_note_summaries,
    _phase7a_pn68_status,
    build_note_classification_copy_ready_prompt,
    build_phase7a_classification_prompt_preview,
    build_phase7a_classification_validator_contract,
    classification_taxonomy,
    classification_taxonomy_audit,
    note_classification_output_schema,
)

from .loading import (
    load_merged_saved_note_correction_review,
    load_saved_note_classification_review,
)

from .normalization import (
    _hash_json_for_contract,
    _item_key,
)

from .safety import (
    review_pipeline_safety_flags,
)

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
