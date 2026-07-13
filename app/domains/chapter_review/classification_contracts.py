"""Chapter review classification contracts responsibilities."""

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

from .safety import (
    review_pipeline_safety_flags,
)

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
