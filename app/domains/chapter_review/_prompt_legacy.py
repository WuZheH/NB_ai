from __future__ import annotations

import json
import hashlib
import re
import copy
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

from app.core.paths import DEFAULT_DB_PATH
from app.domains.chapter_review.prompts import (
    _anchor_note_key,
    _candidate_preview,
    _clean_prompt_heading_path,
    _clean_prompt_heading_segment,
    _float_or_none,
    _int_or_none,
    _json_list,
    _matched_chunk_ids,
    _note_key,
    _page_range_label,
    _preview,
    _prompt_context_excerpt,
    _prompt_text_excerpt,
    _prompt_text_was_truncated,
    _sanitize_context_markdown_for_prompt,
    _supporting_evidence_payload,
    _supporting_evidence_preview,
    _warning_item,
)
from app.services.unit_note_object_processing_service import (
    NOTE_ROLE_EVIDENCE_ONLY,
    NOTE_ROLE_USER_NOTE,
    columns,
    connect_readonly,
    document_row,
    document_source_keys,
    note_processing_fields,
    safety_flags,
    table_exists,
)


class ChapterNoteCorrectionPromptError(ValueError):
    pass


ALLOWED_CORRECTION_STATUSES = {"ok", "needs_revision", "misunderstood", "unsupported", "unclear"}
ALLOWED_ISSUE_TYPES = {
    "none",
    "factual_error",
    "overgeneralization",
    "unsupported_by_evidence",
    "ambiguous_reference",
    "terminology_confusion",
    "logic_gap",
    "alignment_uncertain",
    "unmatched",
    "terminology",
    "wording",
    "under_specified",
    "overclaim",
    "evidence_mismatch",
    "unsupported_in_evidence",
    "other",
}
ALLOWED_EVIDENCE_SUPPORT = {
    "supported",
    "partially_supported",
    "unsupported",
    "unclear",
    "strong",
    "partial",
    "weak",
    "none",
    "uncertain",
}
FORBIDDEN_REVIEW_KEYS = {"object_candidates", "relation_candidates", "mechanism_review_candidate"}
CORRECTION_STATUS_NORMALIZATION = {
    "supported": "ok",
    "needs_minor_correction": "needs_revision",
    "alignment_uncertain": "unclear",
    "unsupported": "unsupported",
    "misunderstood": "misunderstood",
    "ok": "ok",
    "needs_revision": "needs_revision",
    "unclear": "unclear",
}
ISSUE_TYPE_NORMALIZATION = {
    "none": "none",
    "terminology_precision": "terminology",
    "typo_or_expression": "wording",
    "note_too_vague": "under_specified",
    "overinterpretation": "overclaim",
    "overgeneralization": "overclaim",
    "normative_claim": "overclaim",
    "incomplete": "under_specified",
    "incomplete_condition": "under_specified",
    "evidence_too_narrow": "under_specified",
    "unmatched": "alignment_uncertain",
    "conceptual_error": "evidence_mismatch",
    "contradicted_by_evidence": "evidence_mismatch",
    "nonsense_or_typo": "other",
    "unsupported_in_evidence": "unsupported_in_evidence",
    "alignment_uncertain": "alignment_uncertain",
    "factual_error": "factual_error",
    "unsupported_by_evidence": "unsupported_by_evidence",
    "ambiguous_reference": "ambiguous_reference",
    "terminology_confusion": "terminology_confusion",
    "logic_gap": "logic_gap",
    "terminology": "terminology",
    "wording": "wording",
    "under_specified": "under_specified",
    "overclaim": "overclaim",
    "evidence_mismatch": "evidence_mismatch",
    "other": "other",
}
EVIDENCE_SUPPORT_NORMALIZATION = {
    "full": "strong",
    "strong": "strong",
    "partial": "partial",
    "weak": "weak",
    "none": "none",
    "uncertain": "uncertain",
    "supported": "strong",
    "partially_supported": "partial",
    "unsupported": "none",
    "unclear": "uncertain",
}
CHATGPT_REVIEW_WARNING_KEYS = {
    "unmatched_user_note",
    "alignment_uncertain",
    "document_resolved_but_no_page_text_match",
}
INTERNAL_PROMPT_WARNING_KEYS = {
    "bbox_present_no_readable_layout_anchor",
    "dry_run_fuzzy_candidate_not_persisted",
}
PROMPT_CHUNK_EVIDENCE_TEXT_LIMIT = 900
PROMPT_SUPPORTING_EVIDENCE_TEXT_LIMIT = 700
PROMPT_LOCAL_CONTEXT_TEXT_LIMIT = 9_000
NOTE_CORRECTION_REVIEW_TABLE = "note_correction_reviews"
NOTE_CORRECTION_REVIEW_ITEM_TABLE = "note_correction_review_items"
NOTE_CORRECTION_SAVE_CONTEXT = "save_note_correction_review_after_user_audit"


def note_correction_dry_run_safety_flags() -> dict[str, bool]:
    safety = safety_flags()
    safety.update(
        {
            "db_write_performed": False,
            "core_db_write_performed": False,
            "zotero_db_write_performed": False,
            "vector_store_write_performed": False,
            "llm_called": False,
            "external_llm_called": False,
            "object_candidates_generated": False,
            "relation_generated": False,
            "mechanism_generated": False,
            "mechanism_draft_written": False,
            "ocr_or_marker_performed": False,
        }
    )
    return safety


def _review_persistence_status(research_db_path: Path) -> dict[str, Any]:
    try:
        with connect_readonly(research_db_path) as conn:
            ready = table_exists(conn, NOTE_CORRECTION_REVIEW_TABLE) and table_exists(conn, NOTE_CORRECTION_REVIEW_ITEM_TABLE)
    except Exception:
        ready = False
    return {
        "schema_ready": ready,
        "schema_status": "ready" if ready else "review_schema_missing",
        "save_confirmation_context": NOTE_CORRECTION_SAVE_CONTEXT,
        "production_migration_required": not ready,
    }


def build_chapter_note_correction_prompt_package(
    *,
    research_db_path: str | Path = DEFAULT_DB_PATH,
    document_id: int,
    chapter_id: int,
    selected_text_preview_chars: int = 320,
) -> dict[str, Any]:
    """Build a dry-run package for manual note correction review.

    This reads only NOTEBOOK_AI data. It does not open Zotero, does not call an
    LLM, and does not write review results.
    """
    research_path = Path(research_db_path)
    with connect_readonly(research_path) as conn:
        document = document_row(conn, document_id)
        if not document:
            raise ChapterNoteCorrectionPromptError(f"document not found: {document_id}")
        chapter = _chapter_row(conn, document_id, chapter_id)
        if not chapter:
            raise ChapterNoteCorrectionPromptError(
                f"chapter not found: document_id={document_id}, chapter_id={chapter_id}"
            )
        source_keys = document_source_keys(conn, document_id)
        chapter_chunks = _chapter_chunks(conn, document_id=document_id, chapter_id=chapter_id)
        notes = _chapter_notes(conn, document_id=document_id, chapter=chapter, source_keys=source_keys)
        chunks = _chunks_by_id(conn, [note.get("matched_chunk_id") for note in notes])

    annotated_notes = [_note_payload(note, chunks, selected_text_preview_chars) for note in notes]
    chapter_markdown, chunk_offsets = _chapter_markdown_from_chunks(chapter_chunks)
    note_anchors = [
        _note_anchor_payload(note, chunk_offsets)
        for note in annotated_notes
    ]
    note_anchor_ids_by_key = {
        _anchor_note_key(anchor): anchor["note_anchor_id"]
        for anchor in note_anchors
        if _anchor_note_key(anchor)
    }
    correction_candidates = [
        _candidate_with_anchor(note, note_anchor_ids_by_key)
        for note in annotated_notes
        if note["note_processing_role"] == NOTE_ROLE_USER_NOTE
    ]
    supporting_evidence = [
        _supporting_evidence_payload(_candidate_with_anchor(note, note_anchor_ids_by_key))
        for note in annotated_notes
        if note["note_processing_role"] == NOTE_ROLE_EVIDENCE_ONLY
    ]
    unmatched_user_notes = [
        note for note in correction_candidates if not note.get("matched_chunk_id")
    ]
    interleaved_markdown_view = _interleaved_markdown_view(chapter_chunks, note_anchors)

    package = {
        "status": "note_correction_prompt_packaged",
        "mode": "r3_chapter_note_correction_prompt_dry_run",
        "review_mode": "full_chapter",
        "dry_run": True,
        "document": {
            "document_id": document_id,
            "title": document.get("title"),
            "document_type": document.get("document_type"),
            "zotero_item_key": source_keys.get("zotero_item_key"),
            "zotero_attachment_key": source_keys.get("zotero_attachment_key"),
        },
        "unit": {
            "unit_type": "book_chapter",
            "chapter_id": chapter_id,
            "chapter_index": chapter.get("chapter_index"),
            "chapter_title": chapter.get("title"),
            "page_start": chapter.get("pdf_page_start"),
            "page_end": chapter.get("pdf_page_end"),
        },
        "chapter_context": {
            "document_id": document_id,
            "chapter_id": chapter_id,
            "chapter_title": chapter.get("title"),
            "page_start": chapter.get("pdf_page_start"),
            "page_end": chapter.get("pdf_page_end"),
            "chapter_markdown": chapter_markdown,
            "chapter_md_text": chapter_markdown,
            "chunk_count": len(chapter_chunks),
            "source_path": source_keys.get("pdf_path"),
            "md_source": "knowledge_chunks.chunk_text",
            "context_scope": "full_chapter_markdown",
            "context_build_method": "knowledge_chunks_joined_in_chapter_order",
            "context_truncation": "none",
        },
        "note_anchors": note_anchors,
        "interleaved_markdown_view": interleaved_markdown_view,
        "notes_summary": {
            "total_notes": len(annotated_notes),
            "user_notes": len(correction_candidates),
            "evidence_only": len(supporting_evidence),
            "correction_candidate_count": len(correction_candidates),
            "supporting_evidence_count": len(supporting_evidence),
            "unmatched_user_note_count": len(unmatched_user_notes),
            "unmatched_user_note_keys": [
                note.get("zotero_annotation_key") for note in unmatched_user_notes
            ],
        },
        "correction_candidates": correction_candidates,
        "supporting_evidence": supporting_evidence,
        "output_schema": note_correction_output_schema(),
        "review_pipeline": {
            "current_gate": "note_correction_review",
            "next_gate": "note_classification_review",
            "required_gates": [
                "note_correction_review",
                "note_classification_review",
                "object_review",
                "mechanism_review",
            ],
        },
        "system_instructions": [
            "Review only the note_correction_review candidates.",
            "Use the prompt-facing local_context, note_anchors, selected_text, note_text, matched chunk evidence, page, and alignment metadata to check whether the user's note is supported.",
            "Evidence-only annotations are supporting_evidence and must not be corrected as user notes.",
            "Keep unmatched candidates in the review, but surface reviewer_warning instead of pretending they are aligned.",
            "Return JSON with exactly one root field: note_correction_review. Do not output root.items, root.summary, or root.note_correction_review_result.",
            "Do not classify notes, generate object candidates, generate mechanisms, write Zotero, or call any external LLM/API.",
        ],
        "user_payload": {
            "task": "note_correction_review",
            "document_id": document_id,
            "chapter_id": chapter_id,
            "review_mode": "full_chapter",
            "scope": {
                "review_mode": "full_chapter",
                "expected_count": len(correction_candidates),
                "expected_note_ids": [_candidate_canonical_key(item) for item in correction_candidates],
            },
            "chapter_context": {
                "context_scope": "full_chapter_markdown",
                "chapter_markdown_ref": "package.chapter_context.chapter_markdown",
            },
            "note_anchors_ref": "package.note_anchors",
            "correction_candidates_ref": "package.correction_candidates",
            "supporting_evidence_ref": "package.supporting_evidence",
        },
        "prompt_size_strategy": _prompt_size_strategy(
            chapter_markdown,
            interleaved_markdown_view,
            correction_candidates,
        ),
        "zotero_boundary": {
            "zotero_db_access": "not_opened",
            "zotero_db_write_performed": False,
            "zotero_notes_modified": False,
        },
        "prompt_generated_for_manual_copy": True,
        "note_classification_package_generated": False,
        "scope_id": "full_chapter",
        "scope_title": chapter.get("title") or "full chapter",
        "expected_count": len(correction_candidates),
        "scoped_candidate_count": len(correction_candidates),
        "scoped_chunk_count": len(chapter_chunks),
        "estimated_scoped_prompt_chars": 0,
        "full_chapter_repeated": True,
        "review_persistence": _review_persistence_status(research_path),
        **note_correction_dry_run_safety_flags(),
    }
    package["copy_ready_prompt"] = build_note_correction_copy_ready_prompt(package)
    package["estimated_scoped_prompt_chars"] = len(str(package["copy_ready_prompt"] or ""))
    return package


def build_chapter_note_correction_package_preview_response(
    package: Mapping[str, Any],
    *,
    document_id: int,
    chapter_id: int,
) -> dict[str, Any]:
    summary = package.get("notes_summary") or {}
    chapter_context = package.get("chapter_context") or {}
    interleaved_view = str(package.get("interleaved_markdown_view") or "")
    unmatched_keys = list(summary.get("unmatched_user_note_keys") or [])
    safety = note_correction_dry_run_safety_flags()
    local_context = str(chapter_context.get("local_context") or "")
    chapter_markdown = str(chapter_context.get("chapter_markdown") or chapter_context.get("chapter_md_text") or "")
    scope_metadata = dict(package.get("scope_metadata") or {})
    scoped_chunk_count = scope_metadata.get("scoped_chunk_count", chapter_context.get("scoped_chunk_count"))
    display_chunk_count = scoped_chunk_count if scoped_chunk_count is not None else chapter_context.get("chunk_count")
    return {
        "status": "ok",
        "implementation_status": "connected",
        "mode": "r3_chapter_note_correction_package_preview",
        "document_id": document_id,
        "chapter_id": chapter_id,
        "review_mode": package.get("review_mode") or "full_chapter",
        "scope": package.get("scope"),
        "scope_metadata": scope_metadata,
        "package_metadata": {
            "document": package.get("document") or {},
            "unit": package.get("unit") or {},
            "dry_run": bool(package.get("dry_run")),
            "review_gate": "note_correction_review",
            "manual_copy_required": True,
            "scope_metadata": scope_metadata,
        },
        "candidate_count": int(summary.get("correction_candidate_count") or 0),
        "supporting_evidence_count": int(summary.get("supporting_evidence_count") or 0),
        "unmatched_warning_count": int(summary.get("unmatched_user_note_count") or 0),
        "unmatched_warning_keys": unmatched_keys,
        "chapter_context_summary": {
            "context_scope": chapter_context.get("context_scope"),
            "has_chapter_markdown": bool(chapter_markdown),
            "chapter_markdown_chars": len(chapter_markdown),
            "has_local_context": bool(local_context),
            "local_context_chars": len(local_context),
            "chunk_count": display_chunk_count,
            "scoped_chunk_count": scoped_chunk_count,
            "source_path": chapter_context.get("source_path"),
            "md_source": chapter_context.get("md_source"),
        },
        "expected_count": int(package.get("expected_count") or scope_metadata.get("expected_count") or 0),
        "scoped_candidate_count": int(package.get("scoped_candidate_count") or scope_metadata.get("scoped_candidate_count") or 0),
        "scoped_chunk_count": scoped_chunk_count,
        "estimated_scoped_prompt_chars": int(
            package.get("estimated_scoped_prompt_chars")
            or scope_metadata.get("estimated_scoped_prompt_chars")
            or 0
        ),
        "full_chapter_repeated": bool(package.get("full_chapter_repeated")),
        "note_anchor_count": len(package.get("note_anchors") or []),
        "interleaved_markdown_view_summary": {
            "available": bool(interleaved_view.strip()),
            "char_count": len(interleaved_view),
            "fallback_reason": None if interleaved_view.strip() else "chapter_chunks_unavailable",
        },
        "prompt_size_strategy": package.get("prompt_size_strategy") or {},
        "warning_summary": {
            "unmatched_user_note_keys": unmatched_keys,
            "pn68yptt": _warning_item(package, "PN68YPTT"),
        },
        "review_persistence": package.get("review_persistence") or {
            "schema_ready": False,
            "schema_status": "review_schema_missing",
            "save_confirmation_context": NOTE_CORRECTION_SAVE_CONTEXT,
        },
        "preview_candidates": [
            _candidate_preview(item)
            for item in (package.get("correction_candidates") or [])[:3]
        ],
        "supporting_evidence_preview": [
            _supporting_evidence_preview(item)
            for item in (package.get("supporting_evidence") or [])[:3]
        ],
        "copy_ready_prompt": package.get("copy_ready_prompt"),
        "package_json": package,
        "safety_flags": safety,
        **safety,
    }


def build_chapter_note_correction_review_plan(
    *,
    research_db_path: str | Path = DEFAULT_DB_PATH,
    document_id: int,
    chapter_id: int,
    full_prompt_threshold_chars: int = 180_000,
) -> dict[str, Any]:
    package = build_chapter_note_correction_prompt_package(
        research_db_path=research_db_path,
        document_id=document_id,
        chapter_id=chapter_id,
    )
    chapter_chunks = _load_chapter_chunks(
        research_db_path=research_db_path,
        document_id=document_id,
        chapter_id=chapter_id,
    )
    sections, _chunk_section_map = _note_correction_sections(package, chapter_chunks)
    candidates = list(package.get("correction_candidates") or [])
    summary = package.get("notes_summary") or {}
    unmatched_keys = list(summary.get("unmatched_user_note_keys") or [])
    estimated_full_prompt_chars = len(str(package.get("copy_ready_prompt") or ""))
    section_coverage = _section_coverage(sections, total_count=len(candidates))
    oversized_sections = [section for section in sections if int(section.get("candidate_count") or 0) > 20]

    if len(candidates) <= 20 and estimated_full_prompt_chars <= full_prompt_threshold_chars:
        recommended_mode = "full_chapter"
    elif len(candidates) > 20 and len(sections) >= 2 and section_coverage >= 0.8:
        recommended_mode = "section_scoped"
    else:
        recommended_mode = "fixed_size_batch"

    reason_parts = [
        f"本章 {len(candidates)} 条笔记",
        f"完整包约 {estimated_full_prompt_chars} 字符，输入长",
    ]
    if len(sections) >= 2 and section_coverage >= 0.8:
        reason_parts.append(f"小节结构清楚，section coverage={section_coverage:.0%}")
    else:
        reason_parts.append(f"小节结构覆盖不足，section coverage={section_coverage:.0%}")
    if oversized_sections:
        reason_parts.append("存在小节超过 20 条，建议该小节内继续 batch")
    if unmatched_keys:
        reason_parts.append(f"{', '.join(unmatched_keys)} 存在 unmatched warning，需重点审核其所在 section/batch")

    safety = note_correction_dry_run_safety_flags()
    return {
        "status": "ok",
        "mode": "r3_note_correction_review_plan_dry_run",
        "document_id": document_id,
        "chapter_id": chapter_id,
        "chapter_title": (package.get("unit") or {}).get("chapter_title"),
        "total_candidate_count": len(candidates),
        "supporting_evidence_count": int(summary.get("supporting_evidence_count") or 0),
        "unmatched_warning_keys": unmatched_keys,
        "estimated_full_prompt_chars": estimated_full_prompt_chars,
        "estimated_full_prompt_tokens_rough": max(1, estimated_full_prompt_chars // 4),
        "section_count": len(sections),
        "section_coverage": section_coverage,
        "sections": sections,
        "recommended_mode": recommended_mode,
        "recommendation_reason": "；".join(reason_parts),
        "available_modes": ["full_chapter", "section_scoped", "fixed_size_batch"],
        "batch_plans": {
            str(size): _fixed_size_batch_plan(candidates, batch_size=size)
            for size in [10, 15, 20]
        },
        "safety_flags": safety,
        **safety,
    }


def build_chapter_note_correction_sections(
    *,
    research_db_path: str | Path = DEFAULT_DB_PATH,
    document_id: int,
    chapter_id: int,
) -> dict[str, Any]:
    plan = build_chapter_note_correction_review_plan(
        research_db_path=research_db_path,
        document_id=document_id,
        chapter_id=chapter_id,
    )
    return {
        "status": "ok",
        "mode": "r3_note_correction_sections_dry_run",
        "document_id": document_id,
        "chapter_id": chapter_id,
        "chapter_title": plan.get("chapter_title"),
        "section_count": plan.get("section_count"),
        "section_coverage": plan.get("section_coverage"),
        "sections": plan.get("sections") or [],
        "pn68_section_id": _pn68_scope_id(plan.get("sections") or []),
        "unmatched_warning_keys": plan.get("unmatched_warning_keys") or [],
        "safety_flags": plan.get("safety_flags") or note_correction_dry_run_safety_flags(),
        **note_correction_dry_run_safety_flags(),
    }


def build_chapter_note_correction_scoped_package(
    *,
    research_db_path: str | Path = DEFAULT_DB_PATH,
    document_id: int,
    chapter_id: int,
    review_mode: str = "full_chapter",
    section_id: str | None = None,
    batch_size: int = 15,
    batch_index: int = 0,
) -> dict[str, Any]:
    if review_mode == "full_chapter":
        package = build_chapter_note_correction_prompt_package(
            research_db_path=research_db_path,
            document_id=document_id,
            chapter_id=chapter_id,
        )
        package["review_mode"] = "full_chapter"
        package["copy_ready_prompt"] = build_note_correction_scoped_copy_ready_prompt(package)
        package["estimated_scoped_prompt_chars"] = len(str(package.get("copy_ready_prompt") or ""))
        return package

    base = build_chapter_note_correction_prompt_package(
        research_db_path=research_db_path,
        document_id=document_id,
        chapter_id=chapter_id,
    )
    chapter_chunks = _load_chapter_chunks(
        research_db_path=research_db_path,
        document_id=document_id,
        chapter_id=chapter_id,
    )
    sections, chunk_section_map = _note_correction_sections(base, chapter_chunks)
    candidates = list(base.get("correction_candidates") or [])
    note_anchors = list(base.get("note_anchors") or [])
    supporting_evidence = list(base.get("supporting_evidence") or [])

    if review_mode == "section_scoped":
        if not section_id:
            section_id = str((sections[0] or {}).get("section_id") or "") if sections else ""
        section = next((item for item in sections if item.get("section_id") == section_id), None)
        if not section:
            raise ChapterNoteCorrectionPromptError(f"section not found: {section_id}")
        scope_note_ids = set(section.get("note_ids") or [])
        scoped_candidates = [
            candidate for candidate in candidates if _candidate_canonical_key(candidate) in scope_note_ids
        ]
        scoped_chunks = [
            chunk for chunk in chapter_chunks if chunk_section_map.get(_int_or_none(chunk.get("id"))) == section_id
        ]
        scoped_supporting = [
            item for item in supporting_evidence if _scope_for_note_like(item, chunk_section_map, sections) == section_id
        ]
        scope_id = section_id
        scope_title = str(section.get("section_title") or section_id)
        scope = {
            "review_mode": "section_scoped",
            "section_id": section_id,
            "section_label": section.get("section_label"),
            "section_title": section.get("section_title"),
            "heading_path": section.get("heading_path"),
            "page_start": section.get("page_start"),
            "page_end": section.get("page_end"),
            "sort_key": section.get("sort_key"),
            "source": section.get("source"),
            "warnings": list(section.get("warnings") or []),
            "expected_count": len(scoped_candidates),
            "expected_note_ids": [_candidate_canonical_key(item) for item in scoped_candidates],
            "pn68_in_scope": "PN68YPTT" in (section.get("zotero_annotation_keys") or []),
        }
        context_scope = "section_scoped_local_context"
        context_markdown = _scoped_markdown_from_chunks(scoped_chunks)
        neighbor_context_summary = _neighbor_context_summary(scoped_chunks, scoped_candidates)
        interleaved = _scoped_interleaved_markdown(scoped_chunks, note_anchors, set(scope["expected_note_ids"]))
    elif review_mode == "fixed_size_batch":
        if batch_size not in {10, 15, 20}:
            raise ChapterNoteCorrectionPromptError("batch_size must be 10, 15, or 20")
        batches = _fixed_size_batch_plan(candidates, batch_size=batch_size)
        if batch_index < 0 or batch_index >= len(batches):
            raise ChapterNoteCorrectionPromptError(f"batch_index out of range: {batch_index}")
        batch = batches[batch_index]
        scope_note_ids = set(batch.get("note_ids") or [])
        scoped_candidates = [
            candidate for candidate in candidates if _candidate_canonical_key(candidate) in scope_note_ids
        ]
        scoped_chunk_ids = {
            _int_or_none(candidate.get("matched_chunk_id"))
            for candidate in scoped_candidates
            if _int_or_none(candidate.get("matched_chunk_id")) is not None
        }
        scoped_chunks = [
            chunk for chunk in chapter_chunks if _int_or_none(chunk.get("id")) in scoped_chunk_ids
        ]
        scoped_supporting = [
            item for item in supporting_evidence if _int_or_none(item.get("matched_chunk_id")) in scoped_chunk_ids
        ]
        scope_id = str(batch.get("batch_id") or f"batch_{batch_size}_{batch_index}")
        scope_title = f"batch {batch_index + 1} / {len(batches)} (size {batch_size})"
        scope = {
            "review_mode": "fixed_size_batch",
            "batch_id": scope_id,
            "batch_size": batch_size,
            "batch_index": batch_index,
            "batch_count": len(batches),
            "expected_count": len(scoped_candidates),
            "expected_note_ids": [_candidate_canonical_key(item) for item in scoped_candidates],
            "pn68_in_scope": "PN68YPTT" in (batch.get("zotero_annotation_keys") or []),
        }
        context_scope = "fixed_size_batch_local_context"
        chunk_context = _scoped_markdown_from_chunks(scoped_chunks)
        batch_summary = _batch_context_summary(scoped_candidates)
        context_markdown = "\n\n".join(part for part in [batch_summary, chunk_context] if part)
        neighbor_context_summary = _neighbor_context_summary(scoped_chunks, scoped_candidates)
        interleaved = _scoped_interleaved_markdown(scoped_chunks, note_anchors, set(scope["expected_note_ids"]))
    else:
        raise ChapterNoteCorrectionPromptError(f"unsupported note correction review mode: {review_mode}")

    scoped_note_ids = set(scope["expected_note_ids"])
    scoped_anchors = [
        anchor for anchor in note_anchors if _anchor_primary_note_id(anchor) in scoped_note_ids
    ]
    scoped_chunk_count = len(scoped_chunks)
    estimated_scoped_prompt_chars = _estimated_scoped_prompt_chars(
        context_markdown,
        interleaved,
        scoped_candidates,
        scoped_supporting,
    )
    scope_metadata = {
        "mode": review_mode,
        "scope_id": scope_id,
        "scope_title": scope_title,
        "expected_count": len(scoped_candidates),
        "scoped_candidate_count": len(scoped_candidates),
        "scoped_chunk_count": scoped_chunk_count,
        "estimated_scoped_prompt_chars": estimated_scoped_prompt_chars,
        "full_chapter_repeated": False,
    }
    scope.update(scope_metadata)
    base_context = dict(base.get("chapter_context") or {})
    safety = note_correction_dry_run_safety_flags()
    package = {
        "status": "note_correction_prompt_packaged",
        "mode": f"r3_chapter_note_correction_{review_mode}_package_dry_run",
        "dry_run": True,
        "review_mode": review_mode,
        "document": dict(base.get("document") or {}),
        "unit": dict(base.get("unit") or {}),
        "scope": scope,
        "scope_metadata": scope_metadata,
        "scope_id": scope_id,
        "scope_title": scope_title,
        "expected_count": len(scoped_candidates),
        "scoped_candidate_count": len(scoped_candidates),
        "scoped_chunk_count": scoped_chunk_count,
        "estimated_scoped_prompt_chars": estimated_scoped_prompt_chars,
        "full_chapter_repeated": False,
        "chapter_context": {
            "document_id": document_id,
            "chapter_id": chapter_id,
            "chapter_title": base_context.get("chapter_title"),
            "page_start": scope.get("page_start") or base_context.get("page_start"),
            "page_end": scope.get("page_end") or base_context.get("page_end"),
            "context_scope": context_scope,
            "local_context": context_markdown,
            "neighbor_context_summary": neighbor_context_summary,
            "scoped_chunk_count": scoped_chunk_count,
            "source_path": base_context.get("source_path"),
            "md_source": "knowledge_chunks.chunk_text",
            "context_build_method": f"{review_mode}_local_chunks",
            "context_truncation": "scoped_no_full_chapter_repeat",
        },
        "note_anchors": scoped_anchors,
        "interleaved_markdown_view": interleaved,
        "notes_summary": {
            "total_notes": len(scoped_candidates) + len(scoped_supporting),
            "user_notes": len(scoped_candidates),
            "evidence_only": len(scoped_supporting),
            "correction_candidate_count": len(scoped_candidates),
            "supporting_evidence_count": len(scoped_supporting),
            "unmatched_user_note_count": sum(1 for item in scoped_candidates if not item.get("matched_chunk_id")),
            "unmatched_user_note_keys": [
                item.get("zotero_annotation_key")
                for item in scoped_candidates
                if not item.get("matched_chunk_id")
            ],
        },
        "correction_candidates": scoped_candidates,
        "supporting_evidence": scoped_supporting,
        "local_supporting_evidence": scoped_supporting,
        "output_schema": note_correction_review_return_schema(),
        "review_pipeline": {
            "current_gate": "note_correction_review",
            "next_gate": "note_classification_review_locked",
            "required_gates": ["note_correction_review"],
        },
        "system_instructions": [
            "Review only the current scope correction_candidates.",
            "Return exactly scope.expected_count note_correction_review items.",
            "Do not return notes outside this scope.",
            "Do not classify notes, generate object candidates, generate relations, generate mechanisms, write Zotero, or call any external LLM/API.",
        ],
        "user_payload": {
            "task": "note_correction_review",
            "document_id": document_id,
            "chapter_id": chapter_id,
            "review_mode": review_mode,
            "scope": scope,
            "chapter_context": {
                "context_scope": context_scope,
                "local_context_ref": "package.chapter_context.local_context",
                "neighbor_context_summary_ref": "package.chapter_context.neighbor_context_summary",
            },
            "note_anchors_ref": "package.note_anchors",
            "correction_candidates_ref": "package.correction_candidates",
            "supporting_evidence_ref": "package.local_supporting_evidence",
        },
        "prompt_size_strategy": _scoped_prompt_size_strategy(
            review_mode=review_mode,
            estimated_scoped_prompt_chars=estimated_scoped_prompt_chars,
        ),
        "zotero_boundary": {
            "zotero_db_access": "not_opened",
            "zotero_db_write_performed": False,
            "zotero_notes_modified": False,
        },
        "prompt_generated_for_manual_copy": True,
        "note_classification_package_generated": False,
        "review_persistence": _review_persistence_status(Path(research_db_path)),
        **safety,
    }
    package["copy_ready_prompt"] = build_note_correction_scoped_copy_ready_prompt(package)
    package["raw_estimated_scoped_prompt_chars_before_slimming"] = estimated_scoped_prompt_chars
    package["estimated_scoped_prompt_chars"] = len(str(package.get("copy_ready_prompt") or ""))
    package["scope_metadata"]["estimated_scoped_prompt_chars"] = package["estimated_scoped_prompt_chars"]
    package["prompt_size_strategy"]["estimated_scoped_prompt_chars"] = package["estimated_scoped_prompt_chars"]
    package["prompt_size_strategy"]["estimated_prompt_chars_without_schema"] = package["estimated_scoped_prompt_chars"]
    return package


def build_chapter_note_correction_canary_subscope_package(
    *,
    research_db_path: str | Path = DEFAULT_DB_PATH,
    document_id: int,
    chapter_id: int,
    parent_review_mode: str = "section_scoped",
    parent_scope_id: str | None = None,
    selected_server_note_ids: list[str] | tuple[str, ...] | None = None,
    selected_note_ids: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    selected = [
        str(value or "").strip()
        for value in (selected_server_note_ids or selected_note_ids or [])
        if str(value or "").strip()
    ]
    if len(selected) < 1 or len(selected) > 3:
        raise ChapterNoteCorrectionPromptError("canary_subscope selected_server_note_ids count must be 1-3")
    if len(set(selected)) != len(selected):
        raise ChapterNoteCorrectionPromptError("canary_subscope selected_server_note_ids must be unique")
    if parent_review_mode != "section_scoped":
        raise ChapterNoteCorrectionPromptError("canary_subscope currently requires parent_review_mode=section_scoped")
    if not parent_scope_id:
        raise ChapterNoteCorrectionPromptError("canary_subscope parent_scope_id is required")

    parent = build_chapter_note_correction_scoped_package(
        research_db_path=research_db_path,
        document_id=document_id,
        chapter_id=chapter_id,
        review_mode="section_scoped",
        section_id=parent_scope_id,
    )
    parent_candidates = list(parent.get("correction_candidates") or [])
    candidates_by_server_id = {
        str(candidate.get("server_note_id") or "").strip(): candidate
        for candidate in parent_candidates
        if str(candidate.get("server_note_id") or "").strip()
    }
    outside = [note_id for note_id in selected if note_id not in candidates_by_server_id]
    if outside:
        raise ChapterNoteCorrectionPromptError(
            f"canary_subscope selected_server_note_ids outside parent scope: {', '.join(outside[:3])}"
        )

    scoped_candidates = [copy.deepcopy(candidates_by_server_id[note_id]) for note_id in selected]
    selected_set = set(selected)
    scoped_anchors = [
        copy.deepcopy(anchor)
        for anchor in parent.get("note_anchors") or []
        if str(anchor.get("server_note_id") or "").strip() in selected_set
    ]
    selected_keys = [str(candidate.get("zotero_annotation_key") or "") for candidate in scoped_candidates]
    contains_pn68 = "PN68YPTT" in selected_keys
    parent_scope = dict(parent.get("scope") or {})
    source_hash = _canary_subscope_source_hash(
        document_id=document_id,
        chapter_id=chapter_id,
        parent_review_mode=parent_review_mode,
        parent_scope_id=parent_scope_id,
        selected_server_note_ids=selected,
    )
    parent_hash = _canary_parent_scope_hash(parent)
    scope_id = f"canary_{parent_scope_id}_{source_hash[:12]}"
    scope = {
        "review_mode": "canary_subscope",
        "scope_id": scope_id,
        "canary_subscope": True,
        "is_canary_subscope": True,
        "parent_review_mode": parent_review_mode,
        "parent_scope_id": parent_scope_id,
        "parent_section_id": parent_scope_id,
        "parent_scope_title": parent.get("scope_title") or parent_scope.get("scope_title"),
        "parent_scope_expected_count": len(parent_candidates),
        "original_scope_expected_count": len(parent_candidates),
        "selected_server_note_ids": selected,
        "selected_note_ids": selected,
        "expected_count": len(scoped_candidates),
        "expected_note_ids": [str(candidate.get("server_note_id") or "") for candidate in scoped_candidates],
        "canary_selected_count": len(scoped_candidates),
        "pn68_in_scope": contains_pn68,
        "zotero_annotation_keys": selected_keys,
        "source_package_hash": source_hash,
        "parent_package_hash": parent_hash,
        "page_start": parent_scope.get("page_start"),
        "page_end": parent_scope.get("page_end"),
        "warnings": list(parent_scope.get("warnings") or []),
    }
    scope_metadata = {
        "mode": "canary_subscope",
        "scope_id": scope_id,
        "scope_title": f"canary subscope of {parent_scope_id}",
        "expected_count": len(scoped_candidates),
        "scoped_candidate_count": len(scoped_candidates),
        "canary_subscope": True,
        "is_canary_subscope": True,
        "parent_review_mode": parent_review_mode,
        "parent_scope_id": parent_scope_id,
        "parent_scope_expected_count": len(parent_candidates),
        "original_scope_expected_count": len(parent_candidates),
        "canary_selected_count": len(scoped_candidates),
        "selected_server_note_ids": selected,
        "source_package_hash": source_hash,
        "parent_package_hash": parent_hash,
    }

    package = copy.deepcopy(parent)
    package.update(
        {
            "mode": "r3_chapter_note_correction_canary_subscope_package_dry_run",
            "review_mode": "canary_subscope",
            "scope": scope,
            "scope_metadata": scope_metadata,
            "scope_id": scope_id,
            "scope_title": scope_metadata["scope_title"],
            "expected_count": len(scoped_candidates),
            "scoped_candidate_count": len(scoped_candidates),
            "correction_candidates": scoped_candidates,
            "note_anchors": scoped_anchors,
            "supporting_evidence": [],
            "local_supporting_evidence": [],
            "canary_subscope": True,
            "is_canary_subscope": True,
            "source_package_hash": source_hash,
            "parent_package_hash": parent_hash,
        }
    )
    package["notes_summary"] = {
        "total_notes": len(scoped_candidates),
        "user_notes": len(scoped_candidates),
        "evidence_only": 0,
        "correction_candidate_count": len(scoped_candidates),
        "supporting_evidence_count": 0,
        "unmatched_user_note_count": sum(1 for item in scoped_candidates if not item.get("matched_chunk_id")),
        "unmatched_user_note_keys": [
            item.get("zotero_annotation_key")
            for item in scoped_candidates
            if not item.get("matched_chunk_id")
        ],
    }
    package["system_instructions"] = [
        "Review only this explicit canary_subscope.",
        "Return exactly scope.canary_selected_count note_correction_review items.",
        "Do not return other notes from the parent scope.",
        "Do not write Zotero, vector store, object candidates, relations, mechanisms, or call any external LLM/API.",
    ]
    package["user_payload"] = {
        "task": "note_correction_review",
        "document_id": document_id,
        "chapter_id": chapter_id,
        "review_mode": "canary_subscope",
        "scope": scope,
        "parent_scope_ref": {
            "parent_review_mode": parent_review_mode,
            "parent_scope_id": parent_scope_id,
        },
        "correction_candidates_ref": "package.correction_candidates",
    }
    package["copy_ready_prompt"] = build_note_correction_scoped_copy_ready_prompt(package)
    package["estimated_scoped_prompt_chars"] = len(str(package.get("copy_ready_prompt") or ""))
    package["scope_metadata"]["estimated_scoped_prompt_chars"] = package["estimated_scoped_prompt_chars"]
    package["prompt_size_strategy"] = _scoped_prompt_size_strategy(
        review_mode="canary_subscope",
        estimated_scoped_prompt_chars=package["estimated_scoped_prompt_chars"],
    )
    return package


def _canary_subscope_source_hash(
    *,
    document_id: int,
    chapter_id: int,
    parent_review_mode: str,
    parent_scope_id: str | None,
    selected_server_note_ids: list[str],
) -> str:
    payload = {
        "document_id": int(document_id),
        "chapter_id": int(chapter_id),
        "parent_review_mode": parent_review_mode,
        "parent_scope_id": parent_scope_id,
        "selected_server_note_ids": sorted(selected_server_note_ids),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def _canary_parent_scope_hash(parent_package: Mapping[str, Any]) -> str:
    candidates = list(parent_package.get("correction_candidates") or [])
    scope = parent_package.get("scope") or {}
    payload = {
        "review_mode": parent_package.get("review_mode"),
        "scope_id": parent_package.get("scope_id") or scope.get("scope_id") or scope.get("section_id"),
        "expected_note_ids": [
            str(candidate.get("server_note_id") or "").strip()
            for candidate in candidates
            if str(candidate.get("server_note_id") or "").strip()
        ],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def build_note_correction_scoped_copy_ready_prompt(package: Mapping[str, Any]) -> str:
    review_mode = str(package.get("review_mode") or "full_chapter")
    scope = package.get("scope") or {}
    candidates = list(package.get("correction_candidates") or [])
    expected_count = len(candidates)
    warning_keys = list((package.get("notes_summary") or {}).get("unmatched_user_note_keys") or [])
    if review_mode == "section_scoped":
        mode_line = (
            f"这是小节审核：scope_id={scope.get('section_id')}，"
            f"expected_count={expected_count}，只审核当前 section，不要返回其他小节 notes。"
        )
    elif review_mode == "fixed_size_batch":
        mode_line = (
            f"这是固定 batch 审核：scope_id={scope.get('batch_id')}，"
            f"batch_index={scope.get('batch_index')}，batch_size={scope.get('batch_size')}，"
            f"expected_count={expected_count}，只审核当前 batch，不要返回 batch 外 notes。"
        )
    elif review_mode == "canary_subscope":
        mode_line = (
            f"这是 production canary 前置子集审核：scope_id={scope.get('scope_id')}，"
            f"parent_scope_id={scope.get('parent_scope_id')}，"
            f"parent_scope_expected_count={scope.get('parent_scope_expected_count')}，"
            f"canary_selected_count={scope.get('canary_selected_count')}，"
            f"expected_count={expected_count}，只返回 selected_server_note_ids 中的 notes。"
        )
    else:
        mode_line = f"这是完整章审核：expected_count={expected_count}，必须返回本章全部 {expected_count} 条 items，不要省略。"
    context_line = (
        "本 prompt 包含当前 scope 的原文 local_context / selected_text / note_text / chunk evidence，不只是笔记。"
        if review_mode == "full_chapter"
        else "本 prompt 包含当前 scope 的原文 local_context / selected_text / note_text / chunk evidence，不只是笔记；不会复制整章 raw/interleaved 视图。"
    )
    pn68_warning = (
        "PN68YPTT 未匹配到 chunk，后续纠错审核需谨慎。本 scope 包含 PN68YPTT unmatched warning，PN68YPTT 必须保留 alignment/unmatched 风险。"
        if "PN68YPTT" in warning_keys or scope.get("pn68_in_scope")
        else "本 scope 不包含 PN68YPTT warning。"
    )
    chatgpt_package = _chatgpt_input_package(package)
    return "\n".join(
        [
            "# NOTEBOOK_AI 笔记纠错审核输入提示词",
            "",
            "## 审核任务说明",
            "这里生成的是发给 ChatGPT 的输入，不是审核结果。请只做 note_correction_review，并返回 JSON。",
            context_line,
            "",
            "## 审核方式",
            mode_line,
            "root 只能有一个字段：note_correction_review。",
            "根对象不要额外输出 items、summary 或任何其他 sibling 字段。",
            "不要在 note_correction_review 外层增加任何字段。",
            "",
            "## 禁止事项",
            "禁止生成 classification/object/relation/mechanism。",
            "禁止 classification。",
            "禁止生成 object_candidates、relation_candidates、mechanism_review_candidate。",
            "禁止生成 object/relation/mechanism 或 insight。",
            "禁止声称已经写入 NOTEBOOK_AI、Zotero、PDF、tags、数据库或 vector store。",
            "",
            "## PN68YPTT unmatched warning",
            pn68_warning,
            "",
            "## 输出 JSON schema",
            json.dumps(note_correction_review_return_example(package), ensure_ascii=False, indent=2),
            "",
            "## 精简 ChatGPT 输入包",
            json.dumps(chatgpt_package, ensure_ascii=False, indent=2),
        ]
    )


def write_chapter_note_correction_prompt_package(
    *,
    output_path: str | Path,
    research_db_path: str | Path = DEFAULT_DB_PATH,
    document_id: int,
    chapter_id: int,
) -> dict[str, Any]:
    package = build_chapter_note_correction_prompt_package(
        research_db_path=research_db_path,
        document_id=document_id,
        chapter_id=chapter_id,
    )
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(package, ensure_ascii=False, indent=2), encoding="utf-8")
    return package


def note_correction_output_schema() -> dict[str, Any]:
    return note_correction_review_return_schema()


def note_correction_review_return_schema() -> dict[str, Any]:
    return {
        "note_correction_review": {
            "review_type": "note_correction_review",
            "document_id": "integer",
            "chapter_id": "integer",
            "summary": {
                "total_items": "integer",
                "correction_status_counts": "object keyed by correction_status",
                "issue_type_counts": "object keyed by issue_type",
                "evidence_support_counts": "object keyed by evidence_support",
                "alignment_warning_count": "integer",
            },
            "items": [
                {
                    "note_id": "string",
                    "server_note_id": "string|null",
                    "client_note_id": "string|null",
                    "zotero_annotation_key": "string|null",
                    "page": "integer|null",
                    "correction_status": "ok|needs_revision|misunderstood|unsupported|unclear",
                    "issue_type": (
                        "none|factual_error|overgeneralization|unsupported_by_evidence|ambiguous_reference|"
                        "terminology_confusion|logic_gap|alignment_uncertain|unmatched|terminology|wording|"
                        "under_specified|overclaim|evidence_mismatch|unsupported_in_evidence|other"
                    ),
                    "explanation": "string",
                    "suggested_revision": "string|null",
                    "evidence_support": "strong|partial|weak|none|uncertain",
                    "confidence": "number_between_0_and_1",
                    "reviewer_warning": "string|null",
                }
            ],
        },
    }


def note_correction_review_return_example(package: Mapping[str, Any] | None = None) -> dict[str, Any]:
    package = package or {}
    document = package.get("document") or {}
    unit = package.get("unit") or {}
    return {
        "note_correction_review": {
            "review_type": "note_correction_review",
            "document_id": int(document.get("document_id") or 0),
            "chapter_id": int(unit.get("chapter_id") or package.get("chapter_id") or 0),
            "summary": {
                "total_items": 1,
                "correction_status_counts": {"ok": 1},
                "issue_type_counts": {"none": 1},
                "evidence_support_counts": {"strong": 1},
                "alignment_warning_count": 0,
            },
            "items": [
                {
                    "note_id": "zinsp_zotero_annotation_example",
                    "server_note_id": "zinsp_zotero_annotation_example",
                    "client_note_id": "zinsp_client_zotero_annotation_example",
                    "zotero_annotation_key": "EXAMPLE",
                    "page": None,
                    "correction_status": "ok",
                    "issue_type": "none",
                    "explanation": "The note is supported by selected_text and chunk_evidence_text.",
                    "suggested_revision": "",
                    "evidence_support": "strong",
                    "confidence": 0.85,
                    "reviewer_warning": "",
                }
            ],
        }
    }


def build_note_correction_copy_ready_prompt(package: Mapping[str, Any]) -> str:
    package_dict = dict(package)
    package_dict.setdefault("review_mode", "full_chapter")
    return build_note_correction_scoped_copy_ready_prompt(package_dict)


def _chatgpt_input_package(package: Mapping[str, Any]) -> dict[str, Any]:
    chapter_context = package.get("chapter_context") or {}
    scope = dict(package.get("scope") or {})
    scope_metadata = dict(package.get("scope_metadata") or {})
    review_mode = str(package.get("review_mode") or scope.get("review_mode") or "full_chapter")
    canonical_heading = _canonical_prompt_heading(package)
    raw_context = str(
        chapter_context.get("local_context")
        or chapter_context.get("chapter_markdown")
        or chapter_context.get("chapter_md_text")
        or ""
    )
    local_context, heading_warnings = _sanitize_context_markdown_for_prompt(
        raw_context,
        canonical_heading=canonical_heading,
        force_canonical=bool(scope.get("review_mode") == "section_scoped"),
    )
    context_limit = PROMPT_LOCAL_CONTEXT_TEXT_LIMIT if review_mode in {"section_scoped", "fixed_size_batch"} else len(local_context)
    prompt_local_context = _prompt_context_excerpt(local_context, context_limit)
    force_canonical_heading = bool(scope.get("review_mode") == "section_scoped")
    candidates = [
        _candidate_prompt_payload(
            item,
            canonical_heading=canonical_heading,
            force_canonical_heading=force_canonical_heading,
        )
        for item in package.get("correction_candidates") or []
    ]
    anchors = [
        _anchor_prompt_payload(
            anchor,
            canonical_heading=canonical_heading,
            force_canonical_heading=force_canonical_heading,
        )
        for anchor in package.get("note_anchors") or []
    ]
    supporting = [
        _supporting_evidence_prompt_payload(
            item,
            canonical_heading=canonical_heading,
            force_canonical_heading=force_canonical_heading,
        )
        for item in (
            package.get("local_supporting_evidence")
            or package.get("supporting_evidence")
            or []
        )
    ]
    return {
        "task": "note_correction_review",
        "review_mode": review_mode,
        "document": package.get("document") or {},
        "unit": package.get("unit") or {},
        "scope_metadata": {
            **scope_metadata,
            "scope": _prompt_scope_payload(scope, canonical_heading),
            "expected_note_ids": scope.get("expected_note_ids")
            or [_candidate_canonical_key(item) for item in package.get("correction_candidates") or []],
            "canonical_heading": canonical_heading,
        },
        "local_context": {
            "heading": canonical_heading,
            "context_scope": chapter_context.get("context_scope"),
            "md_source": chapter_context.get("md_source"),
            "text": prompt_local_context,
            "text_truncated": _prompt_text_was_truncated(local_context, PROMPT_LOCAL_CONTEXT_TEXT_LIMIT),
        },
        "note_anchors": anchors,
        "correction_candidates": candidates,
        "local_supporting_evidence": supporting,
        "output_rules": {
            "root_allowed_keys": ["note_correction_review"],
            "root_shape": "single_root_field_only",
            "do_not_add_root_sibling_fields": True,
            "expected_count": len(candidates),
            "only_current_scope": True,
        },
        "sanitization": {
            "headings_sanitized_for_prompt": bool(heading_warnings),
            "internal_pipeline_warnings_omitted_from_prompt": True,
        },
    }


def _candidate_prompt_payload(
    item: Mapping[str, Any],
    *,
    canonical_heading: str,
    force_canonical_heading: bool,
) -> dict[str, Any]:
    warnings = _review_warning_keys(item)
    heading = canonical_heading if force_canonical_heading else _clean_prompt_heading_path(item.get("chunk_heading_path"), canonical_heading)
    return {
        "note_id": item.get("note_id"),
        "server_note_id": item.get("server_note_id"),
        "client_note_id": item.get("client_note_id"),
        "zotero_annotation_key": item.get("zotero_annotation_key"),
        "page": item.get("page"),
        "note_anchor_id": item.get("note_anchor_id"),
        "selected_text": item.get("selected_text") or "",
        "note_text": item.get("note_text") or "",
        "matched_chunk_id": item.get("matched_chunk_id"),
        "chunk_heading_path": heading,
        "chunk_evidence_text": _prompt_text_excerpt(
            item.get("chunk_evidence_text"),
            PROMPT_CHUNK_EVIDENCE_TEXT_LIMIT,
        ),
        "chunk_evidence_text_truncated": _prompt_text_was_truncated(
            item.get("chunk_evidence_text"),
            PROMPT_CHUNK_EVIDENCE_TEXT_LIMIT,
        ),
        "evidence_alignment_status": item.get("evidence_alignment_status"),
        "alignment_confidence": item.get("alignment_confidence"),
        "warnings": warnings,
        "reviewer_warning": _prompt_reviewer_warning(item, warnings),
    }


def _anchor_prompt_payload(
    anchor: Mapping[str, Any],
    *,
    canonical_heading: str,
    force_canonical_heading: bool,
) -> dict[str, Any]:
    warnings = _review_warning_keys(anchor)
    return {
        "note_anchor_id": anchor.get("note_anchor_id"),
        "server_note_id": anchor.get("server_note_id"),
        "client_note_id": anchor.get("client_note_id"),
        "zotero_annotation_key": anchor.get("zotero_annotation_key"),
        "page": anchor.get("page"),
        "matched_chunk_id": anchor.get("matched_chunk_id"),
        "chunk_heading_path": canonical_heading if force_canonical_heading else _clean_prompt_heading_path(anchor.get("chunk_heading_path"), canonical_heading),
        "anchor_method": anchor.get("anchor_method"),
        "evidence_alignment_status": anchor.get("evidence_alignment_status"),
        "alignment_confidence": anchor.get("alignment_confidence"),
        "warnings": warnings,
    }


def _supporting_evidence_prompt_payload(
    item: Mapping[str, Any],
    *,
    canonical_heading: str,
    force_canonical_heading: bool,
) -> dict[str, Any]:
    heading = canonical_heading if force_canonical_heading else _clean_prompt_heading_path(item.get("chunk_heading_path"), canonical_heading)
    return {
        "evidence_role": item.get("evidence_role") or "supporting_evidence",
        "source_note_id": item.get("source_note_id"),
        "server_note_id": item.get("server_note_id"),
        "client_note_id": item.get("client_note_id"),
        "zotero_annotation_key": item.get("zotero_annotation_key"),
        "page": item.get("page"),
        "selected_text": item.get("selected_text") or "",
        "matched_chunk_id": item.get("matched_chunk_id"),
        "chunk_heading_path": heading,
        "chunk_evidence_text": _prompt_text_excerpt(
            item.get("chunk_evidence_text"),
            PROMPT_SUPPORTING_EVIDENCE_TEXT_LIMIT,
        ),
        "chunk_evidence_text_truncated": _prompt_text_was_truncated(
            item.get("chunk_evidence_text"),
            PROMPT_SUPPORTING_EVIDENCE_TEXT_LIMIT,
        ),
    }


def _prompt_scope_payload(scope: Mapping[str, Any], canonical_heading: str) -> dict[str, Any]:
    clean = {
        key: value
        for key, value in dict(scope).items()
        if key not in {"heading_path", "source_heading", "warnings"}
    }
    if canonical_heading:
        clean["heading_path"] = canonical_heading
    clean["developer_warning_count"] = len(scope.get("warnings") or [])
    return clean


def _review_warning_keys(item: Mapping[str, Any]) -> list[str]:
    warnings = [
        str(warning)
        for warning in item.get("warnings") or []
        if str(warning) in CHATGPT_REVIEW_WARNING_KEYS
    ]
    status = str(item.get("evidence_alignment_status") or "").strip().lower()
    confidence = str(item.get("alignment_confidence") or "").strip().lower()
    confidence_number = _float_or_none(item.get("alignment_confidence"))
    if status in {"unmatched", "low_confidence"}:
        warnings.append(f"evidence_alignment_status={status}")
    if confidence in {"low", "none"}:
        warnings.append("alignment_uncertain")
    if confidence_number is not None and confidence_number < 0.5:
        warnings.append("low confidence alignment")
    if item.get("matched_chunk_id") in {None, ""} and item.get("note_processing_role") == NOTE_ROLE_USER_NOTE:
        warnings.extend(["unmatched_user_note", "alignment_uncertain"])
    return list(dict.fromkeys(warnings))


def _developer_warning_keys(item: Mapping[str, Any]) -> list[str]:
    return [
        str(warning)
        for warning in item.get("warnings") or []
        if str(warning) in INTERNAL_PROMPT_WARNING_KEYS
        or str(warning) not in CHATGPT_REVIEW_WARNING_KEYS
    ]


def _prompt_reviewer_warning(item: Mapping[str, Any], warnings: list[str]) -> str | None:
    if warnings:
        if any("unmatched" in warning or "alignment" in warning for warning in warnings):
            return "alignment_uncertain: verify this note against selected_text and chunk_evidence_text before revising."
    text = str(item.get("reviewer_warning") or "").strip()
    for warning in INTERNAL_PROMPT_WARNING_KEYS:
        text = text.replace(warning, "").replace(",,", ",")
    text = re.sub(r"alignment_warnings=\s*[, ]*", "alignment_warnings=", text).strip(" ;,")
    return text or None


def _canonical_prompt_heading(package: Mapping[str, Any]) -> str:
    unit = package.get("unit") or {}
    scope = package.get("scope") or {}
    chapter_title = str(unit.get("chapter_title") or "").strip()
    section_title = str(scope.get("section_title") or scope.get("scope_title") or "").strip()
    if scope.get("review_mode") == "section_scoped" and chapter_title and section_title:
        return f"{_clean_prompt_heading_path(chapter_title, chapter_title)} / {_clean_prompt_heading_path(section_title, section_title)}"
    return _clean_prompt_heading_path(section_title or chapter_title, chapter_title or section_title)


def validate_chapter_note_correction_review(
    *,
    research_db_path: str | Path = DEFAULT_DB_PATH,
    document_id: int,
    chapter_id: int,
    review_payload: str | Mapping[str, Any],
    expected_package: Mapping[str, Any] | None = None,
    require_pn68: bool = True,
) -> dict[str, Any]:
    package = expected_package or build_chapter_note_correction_prompt_package(
        research_db_path=research_db_path,
        document_id=document_id,
        chapter_id=chapter_id,
    )
    candidates = list(package.get("correction_candidates") or [])
    candidate_index = _candidate_index(candidates)
    expected_keys = {
        _candidate_canonical_key(candidate)
        for candidate in candidates
        if _candidate_canonical_key(candidate)
    }
    errors: list[str] = []
    warnings: list[str] = []
    raw_parsed = _parse_review_payload(review_payload, errors)
    parsed: dict[str, Any] | None = None
    normalization_applied = False
    normalization_warnings: list[str] = []
    normalized_json: dict[str, Any] | None = None
    root_pollution_warning = False
    normalized_items: list[dict[str, Any]] = []
    seen_expected_keys: set[str] = set()
    duplicate_note_ids: set[str] = set()
    unexpected_note_ids: set[str] = set()
    raw_items_count = 0

    if raw_parsed is not None:
        forbidden = sorted(_forbidden_keys(raw_parsed))
        parsed, normalization_result = normalize_chatgpt_note_correction_review(raw_parsed)
        normalization_applied = bool(normalization_result["applied"])
        normalization_warnings = list(normalization_result["warnings"])
        root_pollution_warning = bool(normalization_result.get("root_pollution_warning"))
        normalized_json = parsed
        if forbidden:
            errors.append(f"forbidden review keys present: {', '.join(forbidden)}")
        if parsed.get("review_type") != "note_correction_review":
            errors.append("review_type must be note_correction_review")
        if _int_or_none(parsed.get("document_id")) != int(document_id):
            errors.append(f"document_id must be {document_id}")
        if _int_or_none(parsed.get("chapter_id")) != int(chapter_id):
            errors.append(f"chapter_id must be {chapter_id}")

        raw_items = parsed.get("items")
        if not isinstance(raw_items, list):
            errors.append("items must be an array")
            raw_items = []
        raw_items_count = len(raw_items)
        if len(raw_items) != len(candidates):
            errors.append(f"reason=items_length_mismatch expected={len(candidates)} actual={len(raw_items)}")
            errors.append(f"items count must be {len(candidates)}")

        for index, raw_item in enumerate(raw_items):
            normalized, matched_key, item_unexpected_note_ids = _normalize_review_item(
                raw_item,
                index=index,
                candidate_index=candidate_index,
                errors=errors,
                warnings=warnings,
            )
            if normalized:
                normalized_items.append(normalized)
            if matched_key:
                if matched_key in seen_expected_keys:
                    errors.append(f"items[{index}] duplicates candidate {matched_key}")
                    duplicate_note_ids.add(matched_key)
                seen_expected_keys.add(matched_key)
            unexpected_note_ids.update(item_unexpected_note_ids)

        missing = sorted(expected_keys - seen_expected_keys)
        if missing:
            errors.append(f"missing_candidate_count={len(missing)}")
            errors.append(f"items missing expected candidates: {', '.join(missing[:8])}")
        if duplicate_note_ids:
            errors.append(f"duplicate_note_ids={', '.join(sorted(duplicate_note_ids)[:8])}")
        if unexpected_note_ids:
            errors.append(f"unexpected_note_ids={', '.join(sorted(unexpected_note_ids)[:8])}")
        stats = _review_stats(normalized_items, expected_count=len(candidates))
        stats["missing_candidate_count"] = len(missing)
        stats["items_length_mismatch"] = len(raw_items) != len(candidates)
        stats["missing_note_ids"] = missing
        stats["duplicate_note_ids"] = sorted(duplicate_note_ids)
        stats["unexpected_note_ids"] = sorted(unexpected_note_ids)
        stats["duplicate_note_id_count"] = len(duplicate_note_ids)
        stats["unexpected_note_id_count"] = len(unexpected_note_ids)
        _validate_review_summary(parsed.get("summary"), stats, errors)
        if require_pn68:
            _validate_pn68_warning(normalized_items, errors)
    else:
        stats = _review_stats([], expected_count=len(candidates))
        stats["missing_candidate_count"] = len(candidates)
        stats["items_length_mismatch"] = True
        stats["missing_note_ids"] = sorted(expected_keys)
        stats["duplicate_note_ids"] = []
        stats["unexpected_note_ids"] = []
        stats["duplicate_note_id_count"] = 0
        stats["unexpected_note_id_count"] = 0

    scope = package.get("scope") or {}
    is_canary_subscope = bool(scope.get("is_canary_subscope") or scope.get("canary_subscope"))
    completeness = {
        "expected_count": len(candidates),
        "actual_count": raw_items_count,
        "missing_note_ids": list(stats.get("missing_note_ids") or []),
        "duplicate_note_ids": list(stats.get("duplicate_note_ids") or []),
        "unexpected_note_ids": list(stats.get("unexpected_note_ids") or []),
        "root_pollution_warning": root_pollution_warning,
        "items_length_mismatch": bool(stats.get("items_length_mismatch")),
    }
    if is_canary_subscope:
        completeness.update(
            {
                "is_canary_subscope": True,
                "canary_subscope": True,
                "parent_scope_id": scope.get("parent_scope_id"),
                "parent_scope_expected_count": int(scope.get("parent_scope_expected_count") or 0),
                "original_scope_expected_count": int(scope.get("original_scope_expected_count") or 0),
                "canary_selected_count": int(scope.get("canary_selected_count") or len(candidates)),
                "selected_server_note_ids": list(scope.get("selected_server_note_ids") or []),
            }
        )

    safety = safety_flags()
    safety.update(
        {
            "db_write_performed": False,
            "core_db_write_performed": False,
            "zotero_db_write_performed": False,
            "vector_store_write_performed": False,
            "llm_called": False,
            "external_llm_called": False,
            "object_candidates_generated": False,
            "relation_generated": False,
            "mechanism_generated": False,
            "mechanism_draft_written": False,
            "ocr_or_marker_performed": False,
        }
    )
    return {
        "status": "ok",
        "mode": "note_correction_review_validate_dry_run",
        "review_type": "note_correction_review",
        "review_mode": package.get("review_mode") or "full_chapter",
        "scope": scope,
        "is_canary_subscope": is_canary_subscope,
        "canary_subscope": is_canary_subscope,
        "parent_scope_expected_count": completeness.get("parent_scope_expected_count") if is_canary_subscope else None,
        "canary_selected_count": completeness.get("canary_selected_count") if is_canary_subscope else None,
        "document_id": document_id,
        "chapter_id": chapter_id,
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "normalization_applied": normalization_applied,
        "normalization_warnings": normalization_warnings,
        "root_pollution_warning": root_pollution_warning,
        "stats": stats,
        "completeness": completeness,
        "normalized_preview": normalized_items,
        "normalized_json": normalized_json,
        "validation_note": (
            "校验通过，但尚未写入。下一步需要用户确认后才能保存审核结果。"
            if not errors
            else "校验失败；本阶段不会写入任何审核结果。"
        ),
        "safety_flags": safety,
        **safety,
    }


def validate_chapter_note_correction_section_review(
    *,
    research_db_path: str | Path = DEFAULT_DB_PATH,
    document_id: int,
    chapter_id: int,
    section_id: str,
    review_payload: str | Mapping[str, Any],
) -> dict[str, Any]:
    package = build_chapter_note_correction_scoped_package(
        research_db_path=research_db_path,
        document_id=document_id,
        chapter_id=chapter_id,
        review_mode="section_scoped",
        section_id=section_id,
    )
    payload = validate_chapter_note_correction_review(
        research_db_path=research_db_path,
        document_id=document_id,
        chapter_id=chapter_id,
        review_payload=review_payload,
        expected_package=package,
        require_pn68=bool((package.get("scope") or {}).get("pn68_in_scope")),
    )
    return {
        **payload,
        "mode": "note_correction_review_validate_section_dry_run",
        "review_mode": "section_scoped",
        "section_id": section_id,
    }


def validate_chapter_note_correction_batch_review(
    *,
    research_db_path: str | Path = DEFAULT_DB_PATH,
    document_id: int,
    chapter_id: int,
    batch_size: int,
    batch_index: int,
    review_payload: str | Mapping[str, Any],
) -> dict[str, Any]:
    package = build_chapter_note_correction_scoped_package(
        research_db_path=research_db_path,
        document_id=document_id,
        chapter_id=chapter_id,
        review_mode="fixed_size_batch",
        batch_size=batch_size,
        batch_index=batch_index,
    )
    payload = validate_chapter_note_correction_review(
        research_db_path=research_db_path,
        document_id=document_id,
        chapter_id=chapter_id,
        review_payload=review_payload,
        expected_package=package,
        require_pn68=bool((package.get("scope") or {}).get("pn68_in_scope")),
    )
    return {
        **payload,
        "mode": "note_correction_review_validate_batch_dry_run",
        "review_mode": "fixed_size_batch",
        "batch_size": batch_size,
        "batch_index": batch_index,
    }


def normalize_chatgpt_note_correction_review(raw: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Normalize common ChatGPT note_correction_review variants.

    The strict validator still runs after this layer. This function only maps
    compatible field names/values and records compatibility warnings.
    """
    warnings: list[str] = []
    changed = False
    source_payload: Mapping[str, Any] = raw
    root_pollution_warning = False
    if isinstance(raw.get("note_correction_review"), Mapping):
        source_payload = dict(raw.get("note_correction_review") or {})
        changed = True
        warnings.append("note_correction_review_wrapper_unwrapped")
        polluted_keys = [key for key in ["items", "summary"] if key in raw]
        if polluted_keys:
            root_pollution_warning = True
            warnings.append(
                "root_pollution_warning=true: root-level items/summary ignored in favor of note_correction_review wrapper"
            )
        for key in ["review_type", "document_id", "chapter_id"]:
            if key not in source_payload and key in raw:
                source_payload = {**dict(source_payload), key: raw[key]}
                warnings.append(f"wrapper_missing_{key}_filled_from_root")

    normalized_items: list[dict[str, Any]] = []
    raw_items = source_payload.get("items") if isinstance(source_payload.get("items"), list) else []
    for index, item in enumerate(raw_items):
        if not isinstance(item, Mapping):
            normalized_items.append(item)
            continue
        normalized = dict(item)
        note_id = _str_or_none(item.get("note_id"))
        if note_id:
            normalized["original_note_id"] = note_id
            if not _str_or_none(item.get("server_note_id")) and not _str_or_none(item.get("client_note_id")):
                if note_id.startswith("zinsp_client_"):
                    normalized["client_note_id"] = note_id
                    warnings.append(f"items[{index}].note_id_mapped_to_client_note_id")
                    changed = True
                elif note_id.startswith("zinsp_"):
                    normalized["server_note_id"] = note_id
                    warnings.append(f"items[{index}].note_id_mapped_to_server_note_id")
                    changed = True
                else:
                    warnings.append(f"items[{index}].note_id_not_mapped_to_primary_note_identity")

        raw_status = _lower_token(item.get("correction_status"))
        mapped_status = CORRECTION_STATUS_NORMALIZATION.get(raw_status)
        if mapped_status:
            if mapped_status != item.get("correction_status"):
                changed = True
                warnings.append(f"items[{index}].correction_status_normalized:{raw_status}->{mapped_status}")
            normalized["correction_status"] = mapped_status

        raw_issue = _lower_token(item.get("issue_type"))
        alignment_review = _lower_token(item.get("alignment_review") or item.get("alignment_status"))
        if not raw_issue:
            raw_issue = "unmatched" if alignment_review == "unmatched" or raw_status == "alignment_uncertain" else "none"
            warnings.append(f"items[{index}].issue_type_defaulted:{raw_issue}")
            changed = True
        mapped_issue = ISSUE_TYPE_NORMALIZATION.get(raw_issue)
        if mapped_issue is None:
            mapped_issue = "other"
            warnings.append(f"items[{index}].issue_type_unknown_mapped_to_other:{raw_issue}")
            changed = True
        if mapped_issue != item.get("issue_type"):
            changed = True
            if raw_issue != mapped_issue:
                warnings.append(f"items[{index}].issue_type_normalized:{raw_issue}->{mapped_issue}")
        normalized["issue_type"] = mapped_issue

        raw_support = _lower_token(item.get("evidence_support"))
        mapped_support = EVIDENCE_SUPPORT_NORMALIZATION.get(raw_support)
        if mapped_support is None:
            mapped_support = "uncertain"
            warnings.append(f"items[{index}].evidence_support_unknown_mapped_to_uncertain:{raw_support or 'missing'}")
            changed = True
        if mapped_support != item.get("evidence_support"):
            changed = True
            if raw_support != mapped_support:
                warnings.append(f"items[{index}].evidence_support_normalized:{raw_support}->{mapped_support}")
        normalized["evidence_support"] = mapped_support

        if not _str_or_none(item.get("explanation")) and _str_or_none(item.get("review_comment")):
            normalized["explanation"] = str(item.get("review_comment") or "").strip()
            warnings.append(f"items[{index}].review_comment_mapped_to_explanation")
            changed = True
        if "suggested_revision" not in item and "suggested_note" in item:
            normalized["suggested_revision"] = item.get("suggested_note") or ""
            warnings.append(f"items[{index}].suggested_note_mapped_to_suggested_revision")
            changed = True
        elif "suggested_revision" not in item:
            normalized["suggested_revision"] = ""
            warnings.append(f"items[{index}].suggested_revision_defaulted_empty")
            changed = True

        reviewer_warning = _str_or_none(item.get("reviewer_warning"))
        if (
            alignment_review == "unmatched"
            or raw_status == "alignment_uncertain"
            or mapped_issue == "alignment_uncertain"
        ):
            warning_text = reviewer_warning or "alignment_uncertain: unmatched or uncertain note alignment; manual review required."
            if "alignment" not in warning_text.lower() and "unmatched" not in warning_text.lower():
                warning_text = f"{warning_text}; alignment_uncertain"
            normalized["reviewer_warning"] = warning_text
            if warning_text != reviewer_warning:
                warnings.append(f"items[{index}].reviewer_warning_added_for_alignment")
                changed = True

        if not _is_confidence_score(item.get("confidence")):
            normalized["confidence"] = _default_confidence(
                correction_status=str(normalized.get("correction_status") or ""),
                evidence_support=str(normalized.get("evidence_support") or ""),
                alignment_review=alignment_review,
                issue_type=str(normalized.get("issue_type") or ""),
            )
            warnings.append(f"items[{index}].confidence_defaulted")
            changed = True

        normalized_items.append(normalized)

    normalized_payload = {
        key: value
        for key, value in dict(source_payload).items()
        if key != "summary"
    }
    normalized_payload["items"] = normalized_items
    normalized_payload["summary"] = _normalized_review_summary(normalized_items)
    if source_payload.get("summary") != normalized_payload["summary"]:
        warnings.append("summary_recomputed_from_normalized_items")
        changed = True
    return normalized_payload, {
        "applied": changed,
        "warnings": warnings,
        "root_pollution_warning": root_pollution_warning,
    }


def _load_chapter_chunks(
    *,
    research_db_path: str | Path,
    document_id: int,
    chapter_id: int,
) -> list[dict[str, Any]]:
    with connect_readonly(Path(research_db_path)) as conn:
        chapter = _chapter_row(conn, document_id, chapter_id)
        if not chapter:
            raise ChapterNoteCorrectionPromptError(
                f"chapter not found: document_id={document_id}, chapter_id={chapter_id}"
            )
        return _chapter_chunks(conn, document_id=document_id, chapter_id=chapter_id)


def _note_correction_sections(
    package: Mapping[str, Any],
    chapter_chunks: list[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[int, str]]:
    chapter_index = _int_or_none((package.get("unit") or {}).get("chapter_index")) or 8
    sections_by_id: dict[str, dict[str, Any]] = {}
    chunk_section_map: dict[int, str] = {}
    current_section_id: str | None = None
    current_section_number = 0
    for chunk in chapter_chunks:
        chunk_id = _int_or_none(chunk.get("id"))
        chunk_order = _int_or_none(chunk.get("chunk_index")) or chunk_id or len(chunk_section_map)
        section_meta = _section_meta_from_heading(str(chunk.get("heading_path") or ""), chapter_index)
        if section_meta:
            section_number = int(section_meta["section_number"])
            if section_number == current_section_number:
                existing = sections_by_id.get(str(section_meta["section_id"]))
                if existing:
                    _maybe_update_section_title(existing, section_meta)
                current_section_id = str(section_meta["section_id"])
            elif section_number == current_section_number + 1 or (
                current_section_number == 0 and section_number <= 2
            ):
                current_section_number = section_number
                current_section_id = str(section_meta["section_id"])
                if current_section_id not in sections_by_id:
                    sections_by_id[current_section_id] = {
                        **section_meta,
                        "candidate_count": 0,
                        "note_ids": [],
                        "zotero_annotation_keys": [],
                        "unmatched_warning_keys": [],
                        "chunk_count": 0,
                        "page_start": chunk.get("pdf_page_start"),
                        "page_end": chunk.get("pdf_page_end"),
                        "sort_key": [
                            _int_or_none(chunk.get("pdf_page_start")) or 1_000_000,
                            chunk_order,
                            section_number,
                        ],
                        "source": section_meta.get("source") or "chunk_heading",
                        "warnings": [],
                    }
            elif section_number > current_section_number + 1:
                if current_section_id and current_section_id in sections_by_id:
                    warning = (
                        "skipped_nonmonotonic_heading_candidate:"
                        f"{section_meta.get('section_label')} from {section_meta.get('heading_path')}"
                    )
                    if warning not in sections_by_id[current_section_id]["warnings"]:
                        sections_by_id[current_section_id]["warnings"].append(warning)
        if chunk_id is not None and current_section_id:
            chunk_section_map[chunk_id] = current_section_id
            section = sections_by_id[current_section_id]
            section["chunk_count"] = int(section.get("chunk_count") or 0) + 1
            section["page_start"] = _min_optional_int(section.get("page_start"), chunk.get("pdf_page_start"))
            section["page_end"] = _max_optional_int(section.get("page_end"), chunk.get("pdf_page_end"))

    sections = list(sections_by_id.values())
    if not sections:
        return [], chunk_section_map

    for candidate in package.get("correction_candidates") or []:
        section_id = _scope_for_note_like(candidate, chunk_section_map, sections)
        if not section_id:
            continue
        section = sections_by_id.get(section_id)
        if not section:
            continue
        note_id = _candidate_canonical_key(candidate)
        if note_id and note_id not in section["note_ids"]:
            section["note_ids"].append(note_id)
        zotero_key = candidate.get("zotero_annotation_key")
        if zotero_key and zotero_key not in section["zotero_annotation_keys"]:
            section["zotero_annotation_keys"].append(zotero_key)
        if not candidate.get("matched_chunk_id") and zotero_key:
            section["unmatched_warning_keys"].append(zotero_key)
        section["candidate_count"] = len(section["note_ids"])

    return [
        {
            **section,
            "has_pn68yptt": "PN68YPTT" in (section.get("zotero_annotation_keys") or []),
        }
        for section in sorted(sections, key=lambda item: item.get("sort_key") or [1_000_000, 1_000_000, 1_000_000])
        if int(section.get("candidate_count") or 0) > 0
    ], chunk_section_map


def _section_meta_from_heading(heading: str, chapter_index: int) -> dict[str, Any] | None:
    candidates = _section_meta_candidates_from_heading(heading, chapter_index)
    return candidates[-1] if candidates else None


def _section_meta_candidates_from_heading(heading: str, chapter_index: int) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for segment in [part.strip() for part in str(heading or "").split("/") if part.strip()]:
        match = re.match(rf"^{chapter_index}\.(\d{{1,2}})(?:\.\d+)*\.?\s+(.+)$", segment)
        if not match:
            match = re.match(
                rf"^{chapter_index}\.\s+[^/]*?\b{chapter_index}\.(\d{{1,2}})(?:\.\d+)*\.?\s+(.+)$",
                segment,
            )
        if not match:
            continue
        section_number = int(match.group(1))
        if section_number > 20:
            continue
        suffix = re.sub(r"\s+", " ", match.group(2) or "").strip(" .")
        section_label = f"{chapter_index}.{section_number}"
        candidates.append(
            {
                "section_id": f"section_{chapter_index}_{section_number}",
                "section_number": section_number,
                "section_label": section_label,
                "section_title": f"{section_label} {suffix}".strip(),
                "heading_path": heading,
                "source": "chunk_heading",
                "source_heading": segment,
            }
        )
    return candidates


def _maybe_update_section_title(section: dict[str, Any], section_meta: Mapping[str, Any]) -> None:
    current = str(section.get("section_title") or "")
    candidate = str(section_meta.get("section_title") or "")
    if len(candidate) > len(current) and candidate.startswith(str(section.get("section_label") or "")):
        section["section_title"] = candidate
        section["heading_path"] = section_meta.get("heading_path") or section.get("heading_path")
        section["source_heading"] = section_meta.get("source_heading") or section.get("source_heading")


def _scope_for_note_like(
    item: Mapping[str, Any],
    chunk_section_map: Mapping[int, str],
    sections: list[Mapping[str, Any]],
) -> str | None:
    matched_chunk_id = _int_or_none(item.get("matched_chunk_id"))
    if matched_chunk_id is not None and matched_chunk_id in chunk_section_map:
        return chunk_section_map[matched_chunk_id]
    heading = str(item.get("chunk_heading_path") or "")
    chapter_index = _chapter_index_from_sections(sections)
    section_meta = _section_meta_from_heading(heading, chapter_index)
    if section_meta:
        section_id = str(section_meta["section_id"])
        if any(str(section.get("section_id") or "") == section_id for section in sections):
            return section_id
    page = _int_or_none(item.get("page"))
    if page is None:
        return None
    candidates = [
        section
        for section in sections
        if _int_or_none(section.get("page_start")) is not None
    ]
    before = [
        section for section in candidates if int(section.get("page_start") or 0) <= page
    ]
    if before:
        return str(before[-1].get("section_id"))
    return str(candidates[0].get("section_id")) if candidates else None


def _chapter_index_from_sections(sections: list[Mapping[str, Any]]) -> int:
    for section in sections:
        label = str(section.get("section_label") or "")
        if "." in label:
            value = _int_or_none(label.split(".", 1)[0])
            if value is not None:
                return value
    return 8


def _section_coverage(sections: list[Mapping[str, Any]], *, total_count: int) -> float:
    if total_count <= 0:
        return 0.0
    assigned = sum(int(section.get("candidate_count") or 0) for section in sections)
    return min(1.0, assigned / total_count)


def _fixed_size_batch_plan(candidates: list[Mapping[str, Any]], *, batch_size: int) -> list[dict[str, Any]]:
    if batch_size <= 0:
        return []
    batches: list[dict[str, Any]] = []
    for batch_index, start in enumerate(range(0, len(candidates), batch_size)):
        items = candidates[start:start + batch_size]
        batches.append(
            {
                "batch_id": f"batch_{batch_size}_{batch_index}",
                "batch_index": batch_index,
                "batch_size": batch_size,
                "candidate_count": len(items),
                "note_ids": [_candidate_canonical_key(item) for item in items],
                "zotero_annotation_keys": [item.get("zotero_annotation_key") for item in items],
                "has_pn68yptt": any(item.get("zotero_annotation_key") == "PN68YPTT" for item in items),
            }
        )
    return batches


def _pn68_scope_id(scopes: list[Mapping[str, Any]]) -> str | None:
    for scope in scopes:
        if scope.get("has_pn68yptt") or "PN68YPTT" in (scope.get("zotero_annotation_keys") or []):
            return str(scope.get("section_id") or scope.get("batch_id") or "")
    return None


def _scoped_markdown_from_chunks(chunks: list[Mapping[str, Any]]) -> str:
    markdown, _offsets = _chapter_markdown_from_chunks(chunks)
    return markdown


def _scoped_interleaved_markdown(
    chunks: list[Mapping[str, Any]],
    note_anchors: list[Mapping[str, Any]],
    expected_note_ids: set[str],
) -> str:
    anchors = [
        anchor for anchor in note_anchors if _anchor_primary_note_id(anchor) in expected_note_ids
    ]
    return _interleaved_markdown_view(chunks, anchors)


def _batch_context_summary(candidates: list[Mapping[str, Any]]) -> str:
    lines = [
        "# Fixed-size batch context summary",
        "This batch includes selected_text, note_text, page, matched_chunk_id, and chunk_evidence_text inside correction_candidates.",
        "Do not use notes outside this batch.",
    ]
    for item in candidates:
        lines.append(
            f"- {item.get('zotero_annotation_key')}: page={item.get('page')} "
            f"matched_chunk_id={item.get('matched_chunk_id')} heading={item.get('chunk_heading_path') or 'unknown'}"
        )
    return "\n".join(lines)


def _neighbor_context_summary(
    chunks: list[Mapping[str, Any]],
    candidates: list[Mapping[str, Any]],
) -> str:
    pages = [
        page for page in [
            _int_or_none(chunk.get("pdf_page_start")) for chunk in chunks
        ] if page is not None
    ]
    headings: list[str] = []
    for chunk in chunks:
        heading = str(chunk.get("heading_path") or "").strip()
        if heading and heading not in headings:
            headings.append(heading)
    page_label = (
        f"pages {min(pages)}-{max(pages)}"
        if pages and min(pages) != max(pages)
        else f"page {pages[0]}" if pages else "pages unknown"
    )
    return "\n".join(
        [
            f"scope_candidate_count={len(candidates)}",
            f"scope_chunk_count={len(chunks)}",
            page_label,
            "headings:",
            *[f"- {heading}" for heading in headings[:8]],
        ]
    )


def _estimated_scoped_prompt_chars(
    local_context: str,
    interleaved_markdown_view: str,
    correction_candidates: list[Mapping[str, Any]],
    supporting_evidence: list[Mapping[str, Any]],
) -> int:
    candidate_chars = sum(
        len(str(item.get("note_text") or ""))
        + len(str(item.get("selected_text") or ""))
        + len(str(item.get("chunk_evidence_text") or ""))
        for item in correction_candidates
    )
    support_chars = sum(
        len(str(item.get("selected_text") or ""))
        + len(str(item.get("chunk_evidence_text") or ""))
        for item in supporting_evidence
    )
    return len(local_context) + len(interleaved_markdown_view) + candidate_chars + support_chars


def _scoped_prompt_size_strategy(
    *,
    review_mode: str,
    estimated_scoped_prompt_chars: int,
) -> dict[str, Any]:
    return {
        "mode": review_mode,
        "default_mode": review_mode,
        "prompt_size_limit_chars": 180_000,
        "estimated_scoped_prompt_chars": estimated_scoped_prompt_chars,
        "estimated_prompt_chars_without_schema": estimated_scoped_prompt_chars,
        "chunked_package_recommended": False,
        "ui_message": f"{review_mode} package uses scoped local_context; full chapter is not repeated",
    }


def _anchor_primary_note_id(anchor: Mapping[str, Any]) -> str:
    return str(anchor.get("server_note_id") or anchor.get("client_note_id") or "").strip()


def _min_optional_int(left: Any, right: Any) -> int | None:
    values = [value for value in [_int_or_none(left), _int_or_none(right)] if value is not None]
    return min(values) if values else None


def _max_optional_int(left: Any, right: Any) -> int | None:
    values = [value for value in [_int_or_none(left), _int_or_none(right)] if value is not None]
    return max(values) if values else None


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


def _candidate_index(candidates: list[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    index: dict[str, Mapping[str, Any]] = {}
    for candidate in candidates:
        for text in _candidate_primary_identity_keys(candidate):
            index[text] = candidate
    return index


def _candidate_canonical_key(candidate: Mapping[str, Any]) -> str:
    keys = _candidate_primary_identity_keys(candidate)
    return keys[0] if keys else ""


def _candidate_primary_identity_keys(candidate: Mapping[str, Any] | None) -> list[str]:
    if not candidate:
        return []
    server_note_id = _str_or_none(candidate.get("server_note_id"))
    client_note_id = _str_or_none(candidate.get("client_note_id"))
    note_id = _str_or_none(candidate.get("note_id"))
    keys: list[str] = []
    for key in [server_note_id, client_note_id]:
        if key and key not in keys:
            keys.append(key)
    if note_id and note_id in {server_note_id, client_note_id} and note_id not in keys:
        keys.append(note_id)
    return keys


def _normalize_review_item(
    raw_item: Any,
    *,
    index: int,
    candidate_index: Mapping[str, Mapping[str, Any]],
    errors: list[str],
    warnings: list[str],
) -> tuple[dict[str, Any] | None, str | None, list[str]]:
    if not isinstance(raw_item, Mapping):
        errors.append(f"items[{index}] must be an object")
        return None, None, []

    server_note_id = _str_or_none(raw_item.get("server_note_id"))
    client_note_id = _str_or_none(raw_item.get("client_note_id"))
    note_id = _str_or_none(raw_item.get("note_id"))
    attempted_zotero_primary = _uses_zotero_annotation_key_as_primary(raw_item)
    if attempted_zotero_primary:
        message = f"items[{index}].zotero_annotation_key is not accepted as primary note identity"
        errors.append(message)
        warnings.append(message)

    candidate = _match_candidate(raw_item, candidate_index)
    matched_key = _candidate_canonical_key(candidate) if candidate else None
    unexpected_note_ids = _unexpected_primary_note_ids(raw_item, candidate_index)
    if candidate is None:
        errors.append(f"items[{index}] does not match any correction candidate")
        candidate = {}
    elif not server_note_id and note_id == _str_or_none(candidate.get("server_note_id")):
        server_note_id = note_id
    elif not client_note_id and note_id == _str_or_none(candidate.get("client_note_id")):
        client_note_id = note_id

    if not server_note_id and not client_note_id:
        errors.append(f"items[{index}] must include server_note_id or client_note_id")

    correction_status = _str_or_none(raw_item.get("correction_status"))
    issue_type = _str_or_none(raw_item.get("issue_type"))
    evidence_support = _str_or_none(raw_item.get("evidence_support"))
    confidence = raw_item.get("confidence")

    if correction_status not in ALLOWED_CORRECTION_STATUSES:
        errors.append(f"items[{index}].correction_status is invalid")
    if issue_type not in ALLOWED_ISSUE_TYPES:
        errors.append(f"items[{index}].issue_type is invalid")
    if evidence_support not in ALLOWED_EVIDENCE_SUPPORT:
        errors.append(f"items[{index}].evidence_support is invalid")
    if not _is_confidence_score(confidence):
        errors.append(f"items[{index}].confidence must be a number from 0 to 1")

    note_id = note_id or _str_or_none(candidate.get("note_id"))
    reviewer_warning = _str_or_none(raw_item.get("reviewer_warning"))
    normalized = {
        "note_id": note_id,
        "original_note_id": _str_or_none(raw_item.get("original_note_id")),
        "server_note_id": server_note_id or _str_or_none(candidate.get("server_note_id")),
        "client_note_id": client_note_id or _str_or_none(candidate.get("client_note_id")),
        "primary_note_id": matched_key or _raw_primary_note_identity(raw_item),
        "matched_expected_note_id": matched_key,
        "zotero_annotation_key": _str_or_none(raw_item.get("zotero_annotation_key"))
        or _str_or_none(candidate.get("zotero_annotation_key")),
        "page": raw_item.get("page") if raw_item.get("page") is not None else candidate.get("page"),
        "correction_status": correction_status,
        "issue_type": issue_type,
        "explanation": str(raw_item.get("explanation") or "").strip(),
        "suggested_revision": raw_item.get("suggested_revision"),
        "evidence_support": evidence_support,
        "confidence": float(confidence) if _is_confidence_score(confidence) else confidence,
        "reviewer_warning": reviewer_warning,
        "has_alignment_warning": _has_alignment_warning(issue_type, reviewer_warning),
    }
    if not normalized["explanation"]:
        warnings.append(f"items[{index}].explanation is empty")
    return normalized, matched_key, unexpected_note_ids


def _match_candidate(
    raw_item: Mapping[str, Any],
    candidate_index: Mapping[str, Mapping[str, Any]],
) -> Mapping[str, Any] | None:
    for key in [
        raw_item.get("server_note_id"),
        raw_item.get("client_note_id"),
        raw_item.get("note_id"),
    ]:
        text = str(key or "").strip()
        if text and text in candidate_index:
            return candidate_index[text]
    return None


def _raw_primary_note_identity(raw_item: Mapping[str, Any]) -> str:
    for key in [
        raw_item.get("server_note_id"),
        raw_item.get("client_note_id"),
        raw_item.get("note_id"),
    ]:
        text = str(key or "").strip()
        if text:
            return text
    return str(raw_item.get("zotero_annotation_key") or "").strip()


def _unexpected_primary_note_ids(
    raw_item: Mapping[str, Any],
    candidate_index: Mapping[str, Mapping[str, Any]],
) -> list[str]:
    unexpected: list[str] = []
    for key in [
        raw_item.get("server_note_id"),
        raw_item.get("client_note_id"),
        raw_item.get("note_id"),
    ]:
        text = str(key or "").strip()
        if text and text not in candidate_index and text not in unexpected:
            unexpected.append(text)
    if not unexpected and not _raw_primary_note_identity_without_zotero_fallback(raw_item):
        zotero_key = str(raw_item.get("zotero_annotation_key") or "").strip()
        if zotero_key:
            unexpected.append(zotero_key)
    return unexpected


def _raw_primary_note_identity_without_zotero_fallback(raw_item: Mapping[str, Any]) -> str:
    for key in [
        raw_item.get("server_note_id"),
        raw_item.get("client_note_id"),
        raw_item.get("note_id"),
    ]:
        text = str(key or "").strip()
        if text:
            return text
    return ""


def _uses_zotero_annotation_key_as_primary(raw_item: Mapping[str, Any]) -> bool:
    zotero_key = str(raw_item.get("zotero_annotation_key") or "").strip()
    if not zotero_key:
        return False
    primary_values = {
        str(raw_item.get("server_note_id") or "").strip(),
        str(raw_item.get("client_note_id") or "").strip(),
        str(raw_item.get("note_id") or "").strip(),
    }
    return zotero_key in primary_values or not any(primary_values)


def _review_stats(items: list[Mapping[str, Any]], *, expected_count: int) -> dict[str, Any]:
    correction_counts = Counter(str(item.get("correction_status") or "") for item in items)
    issue_counts = Counter(str(item.get("issue_type") or "") for item in items)
    support_counts = Counter(str(item.get("evidence_support") or "") for item in items)
    for counter in [correction_counts, issue_counts, support_counts]:
        counter.pop("", None)
    alignment_warning_count = sum(
        1
        for item in items
        if item.get("has_alignment_warning")
        or _has_alignment_warning(
            str(item.get("issue_type") or ""),
            str(item.get("reviewer_warning") or ""),
        )
    )
    return {
        "expected_item_count": expected_count,
        "item_count": len(items),
        "total_reviewed": len(items),
        "correction_status_counts": dict(correction_counts),
        "issue_type_counts": dict(issue_counts),
        "evidence_support_counts": dict(support_counts),
        "ok_count": int(correction_counts.get("ok") or 0),
        "needs_revision_count": int(correction_counts.get("needs_revision") or 0),
        "misunderstood_count": int(correction_counts.get("misunderstood") or 0),
        "unsupported_count": int(correction_counts.get("unsupported") or 0),
        "unclear_count": int(correction_counts.get("unclear") or 0),
        "alignment_warning_count": alignment_warning_count,
        "pn68yptt_present": any(item.get("zotero_annotation_key") == "PN68YPTT" for item in items),
    }


def _normalized_review_summary(items: list[Mapping[str, Any]]) -> dict[str, Any]:
    stats = _review_stats(items, expected_count=len(items))
    return {
        "total_items": stats["item_count"],
        "total_reviewed": stats["total_reviewed"],
        "correction_status_counts": stats["correction_status_counts"],
        "issue_type_counts": stats["issue_type_counts"],
        "evidence_support_counts": stats["evidence_support_counts"],
        "ok_count": stats["ok_count"],
        "needs_revision_count": stats["needs_revision_count"],
        "misunderstood_count": stats["misunderstood_count"],
        "unsupported_count": stats["unsupported_count"],
        "unclear_count": stats["unclear_count"],
        "alignment_warning_count": stats["alignment_warning_count"],
    }


def _lower_token(value: Any) -> str:
    return str(value or "").strip().lower()


def _default_confidence(
    *,
    correction_status: str,
    evidence_support: str,
    alignment_review: str,
    issue_type: str,
) -> float:
    status = _lower_token(correction_status)
    support = _lower_token(evidence_support)
    alignment = _lower_token(alignment_review)
    issue = _lower_token(issue_type)
    if alignment == "unmatched" or issue == "alignment_uncertain" or status == "unclear" or support == "uncertain":
        return 0.35
    if status == "unsupported" or support == "none":
        return 0.7
    if support == "weak":
        return 0.45
    if support == "partial":
        return 0.65
    if status == "needs_revision" and support == "strong":
        return 0.75
    if status == "ok" and support == "strong":
        return 0.85
    return 0.5


def _validate_review_summary(summary: Any, stats: Mapping[str, Any], errors: list[str]) -> None:
    if not isinstance(summary, Mapping):
        errors.append("summary must be an object")
        return
    total = _int_or_none(summary.get("total_items"))
    if total != int(stats["item_count"]):
        errors.append("summary.total_items does not match items length")
    for field in [
        "correction_status_counts",
        "issue_type_counts",
        "evidence_support_counts",
    ]:
        _validate_count_summary(field, summary.get(field), stats.get(field) or {}, errors)
    alignment_warning_count = _int_or_none(summary.get("alignment_warning_count"))
    if alignment_warning_count != int(stats["alignment_warning_count"]):
        errors.append("summary.alignment_warning_count does not match items")


def _validate_count_summary(
    field: str,
    summary_counts: Any,
    actual_counts: Mapping[str, int],
    errors: list[str],
) -> None:
    if not isinstance(summary_counts, Mapping):
        errors.append(f"summary.{field} must be an object")
        return
    keys = set(str(key) for key in summary_counts.keys()) | set(actual_counts.keys())
    for key in sorted(keys):
        expected = int(actual_counts.get(key) or 0)
        actual = _int_or_none(summary_counts.get(key))
        if actual is None:
            actual = 0
        if actual != expected:
            errors.append(f"summary.{field}.{key} does not match items")


def _validate_pn68_warning(items: list[Mapping[str, Any]], errors: list[str]) -> None:
    pn68 = next((item for item in items if item.get("zotero_annotation_key") == "PN68YPTT"), None)
    if pn68 is None:
        errors.append("PN68YPTT item is required")
        return
    if not pn68.get("matched_expected_note_id"):
        errors.append("PN68YPTT must match a legal NOTEBOOK_AI note identity")
    status = str(pn68.get("correction_status") or "")
    issue_type = str(pn68.get("issue_type") or "")
    reviewer_warning = str(pn68.get("reviewer_warning") or "")
    if status == "ok":
        errors.append("PN68YPTT cannot be marked ok because its original alignment is unmatched")
    if status not in {"unclear", "needs_revision"}:
        errors.append("PN68YPTT correction_status must be unclear or needs_revision")
    if not pn68.get("has_alignment_warning"):
        errors.append("PN68YPTT must preserve alignment_uncertain or unmatched risk")
    if issue_type != "alignment_uncertain" and not _has_alignment_warning(issue_type, reviewer_warning):
        errors.append("PN68YPTT issue_type or reviewer_warning must preserve alignment_uncertain or unmatched risk")


def _forbidden_keys(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key) in FORBIDDEN_REVIEW_KEYS:
                found.add(str(key))
            found.update(_forbidden_keys(child))
    elif isinstance(value, list):
        for child in value:
            found.update(_forbidden_keys(child))
    return found


def _has_alignment_warning(issue_type: str | None, reviewer_warning: str | None) -> bool:
    issue = str(issue_type or "").lower()
    warning = str(reviewer_warning or "").lower()
    return (
        "alignment" in issue
        or "unmatched" in issue
        or "alignment" in warning
        or "unmatched" in warning
    )


def _str_or_none(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _is_confidence_score(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if not isinstance(value, (int, float)):
        return False
    return 0 <= float(value) <= 1


def _chapter_row(conn: Any, document_id: int, chapter_id: int) -> dict[str, Any]:
    if not table_exists(conn, "book_chapters"):
        return {}
    selected = [
        name
        for name in ["id", "chapter_index", "title", "pdf_page_start", "pdf_page_end"]
        if name in columns(conn, "book_chapters")
    ]
    if not selected:
        return {}
    row = conn.execute(
        f"SELECT {', '.join(selected)} FROM book_chapters WHERE document_id = ? AND id = ?",
        (document_id, chapter_id),
    ).fetchone()
    return dict(row) if row else {}


def _chapter_notes(
    conn: Any,
    *,
    document_id: int,
    chapter: Mapping[str, Any],
    source_keys: Mapping[str, Any],
) -> list[dict[str, Any]]:
    if not table_exists(conn, "zotero_inspiration_notes"):
        return []
    note_cols = columns(conn, "zotero_inspiration_notes")
    required = {"id", "matched_document_id", "pdf_page", "selected_text", "note_text"}
    if not required.issubset(note_cols):
        return []

    selected = [
        name
        for name in [
            "id",
            "server_note_id",
            "client_note_id",
            "source",
            "zotero_item_key",
            "zotero_attachment_key",
            "zotero_annotation_key",
            "pdf_page",
            "page_label",
            "selected_text",
            "note_text",
            "user_tags_json",
            "bbox_json",
            "matched_document_id",
            "matched_chunk_id",
            "matched_chunk_ids_json",
            "sync_status",
            "match_status",
            "review_status",
            "mechanism_status",
            "evidence_alignment_status",
            "alignment_confidence",
            "alignment_method",
            "alignment_warnings_json",
        ]
        if name in note_cols
    ]
    params: list[Any] = [
        document_id,
        chapter.get("pdf_page_start"),
        chapter.get("pdf_page_end") or chapter.get("pdf_page_start"),
    ]
    clauses = [
        "matched_document_id = ?",
        "pdf_page BETWEEN ? AND ?",
    ]
    if "source" in note_cols:
        clauses.append("source = ?")
        params.append("zotero_native_annotation")
    if source_keys.get("zotero_item_key") and "zotero_item_key" in note_cols:
        clauses.append("zotero_item_key = ?")
        params.append(source_keys["zotero_item_key"])
    if source_keys.get("zotero_attachment_key") and "zotero_attachment_key" in note_cols:
        clauses.append("zotero_attachment_key = ?")
        params.append(source_keys["zotero_attachment_key"])

    rows = conn.execute(
        f"""
        SELECT {', '.join(selected)}
        FROM zotero_inspiration_notes
        WHERE {' AND '.join(clauses)}
        ORDER BY pdf_page, id
        """,
        params,
    ).fetchall()
    return [dict(row) for row in rows]


def _chapter_chunks(conn: Any, *, document_id: int, chapter_id: int) -> list[dict[str, Any]]:
    if not table_exists(conn, "knowledge_chunks"):
        return []
    chunk_cols = columns(conn, "knowledge_chunks")
    if "chapter_id" not in chunk_cols:
        return []
    id_col = "id" if "id" in chunk_cols else "chunk_id"
    selected = [
        name
        for name in [
            id_col,
            "chapter_id",
            "heading_path",
            "chunk_text",
            "pdf_page_start",
            "pdf_page_end",
        ]
        if name in chunk_cols
    ]
    rows = conn.execute(
        f"""
        SELECT {', '.join(selected)}
        FROM knowledge_chunks
        WHERE document_id = ? AND chapter_id = ?
        ORDER BY COALESCE(pdf_page_start, 0), {id_col}
        """,
        (document_id, chapter_id),
    ).fetchall()
    result = []
    for row in rows:
        data = dict(row)
        if id_col != "id":
            data["id"] = data.get(id_col)
        result.append(data)
    return result


def _chapter_markdown_from_chunks(chapter_chunks: list[Mapping[str, Any]]) -> tuple[str, dict[int, dict[str, Any]]]:
    sections: list[str] = []
    offsets: dict[int, dict[str, Any]] = {}
    cursor = 0
    previous_heading = None
    for index, chunk in enumerate(chapter_chunks, start=1):
        chunk_id = _int_or_none(chunk.get("id"))
        heading = str(chunk.get("heading_path") or "").strip()
        text = str(chunk.get("chunk_text") or "").strip()
        parts = []
        if heading and heading != previous_heading:
            parts.append(f"\n\n## {heading}\n")
            previous_heading = heading
        page = _page_range_label(chunk)
        parts.append(f"\n\n<!-- chunk_id={chunk_id or 'unknown'} page={page} index={index} -->\n")
        parts.append(text)
        block = "".join(parts).strip() + "\n"
        start = cursor
        sections.append(block)
        cursor += len(block)
        if chunk_id is not None:
            offsets[chunk_id] = {
                "anchor_start": start,
                "anchor_end": cursor,
                "chunk_text": text,
                "page": page,
            }
    return "\n".join(sections).strip(), offsets


def _note_anchor_payload(note: Mapping[str, Any], chunk_offsets: Mapping[int, Mapping[str, Any]]) -> dict[str, Any]:
    matched_chunk_id = _int_or_none(note.get("matched_chunk_id"))
    offset = chunk_offsets.get(matched_chunk_id) if matched_chunk_id is not None else None
    anchor_method = "chunk_level_anchor" if offset else "unmatched"
    warnings = list(note.get("warnings") or [])
    if anchor_method == "unmatched" and "alignment_uncertain" not in warnings:
        warnings.append("alignment_uncertain")
    note_anchor_id = f"anchor:{note.get('zotero_annotation_key') or note.get('note_id') or note.get('server_note_id')}"
    return {
        "note_anchor_id": note_anchor_id,
        "server_note_id": note.get("server_note_id"),
        "client_note_id": note.get("client_note_id"),
        "zotero_annotation_key": note.get("zotero_annotation_key"),
        "page": note.get("page"),
        "selected_text": note.get("selected_text") or "",
        "note_text": note.get("note_text") or "",
        "matched_chunk_id": matched_chunk_id,
        "matched_chunk_text": note.get("chunk_evidence_text"),
        "anchor_start": offset.get("anchor_start") if offset else None,
        "anchor_end": offset.get("anchor_end") if offset else None,
        "anchor_method": anchor_method,
        "alignment_confidence": note.get("alignment_confidence"),
        "alignment_method": note.get("alignment_method"),
        "evidence_alignment_status": note.get("evidence_alignment_status"),
        "warnings": list(dict.fromkeys(warnings)),
    }


def _candidate_with_anchor(note: Mapping[str, Any], anchor_ids_by_key: Mapping[str, str]) -> dict[str, Any]:
    item = dict(note)
    key = _note_key(note)
    anchor_id = anchor_ids_by_key.get(key) if key else None
    item["note_anchor_id"] = anchor_id
    item["anchor_method"] = "unmatched" if not item.get("matched_chunk_id") else "chunk_level_anchor"
    if item["anchor_method"] == "unmatched":
        warnings = list(item.get("warnings") or [])
        if "alignment_uncertain" not in warnings:
            warnings.append("alignment_uncertain")
        item["warnings"] = list(dict.fromkeys(warnings))
    return item


def _interleaved_markdown_view(chapter_chunks: list[Mapping[str, Any]], note_anchors: list[Mapping[str, Any]]) -> str:
    anchors_by_chunk: dict[int, list[Mapping[str, Any]]] = {}
    unmatched: list[Mapping[str, Any]] = []
    for anchor in note_anchors:
        matched_chunk_id = _int_or_none(anchor.get("matched_chunk_id"))
        if matched_chunk_id is None:
            unmatched.append(anchor)
        else:
            anchors_by_chunk.setdefault(matched_chunk_id, []).append(anchor)

    blocks: list[str] = []
    previous_heading = None
    for index, chunk in enumerate(chapter_chunks, start=1):
        chunk_id = _int_or_none(chunk.get("id"))
        heading = str(chunk.get("heading_path") or "").strip()
        if heading and heading != previous_heading:
            blocks.append(f"## {heading}")
            previous_heading = heading
        blocks.append(f"<!-- chunk_id={chunk_id or 'unknown'} page={_page_range_label(chunk)} index={index} -->")
        blocks.append(str(chunk.get("chunk_text") or "").strip())
        for anchor in anchors_by_chunk.get(chunk_id or -1, []):
            blocks.append(_note_anchor_markdown(anchor))

    if unmatched:
        blocks.append("## Unmatched note anchors")
        for anchor in unmatched:
            blocks.append(_note_anchor_markdown(anchor))
    return "\n\n".join(block for block in blocks if block)


def _note_anchor_markdown(anchor: Mapping[str, Any]) -> str:
    return "\n".join(
        [
            (
                f"[NOTE_ANCHOR note_anchor_id={anchor.get('note_anchor_id')} "
                f"zotero_annotation_key={anchor.get('zotero_annotation_key')} "
                f"page={anchor.get('page')} matched_chunk_id={anchor.get('matched_chunk_id')} "
                f"anchor_method={anchor.get('anchor_method')}]"
            ),
            f"用户笔记：{anchor.get('note_text') or ''}",
            f"选中原文：{anchor.get('selected_text') or ''}",
            f"对齐状态：{anchor.get('evidence_alignment_status') or 'unknown'}; warnings={anchor.get('warnings') or []}",
            "[/NOTE_ANCHOR]",
        ]
    )


def _prompt_size_strategy(
    chapter_markdown: str,
    interleaved_markdown_view: str,
    correction_candidates: list[Mapping[str, Any]],
) -> dict[str, Any]:
    estimated_chars = len(chapter_markdown) + len(interleaved_markdown_view) + sum(
        len(str(item.get("note_text") or "")) + len(str(item.get("selected_text") or ""))
        for item in correction_candidates
    )
    prompt_size_limit_chars = 180_000
    chunked = estimated_chars > prompt_size_limit_chars
    return {
        "default_mode": "full_chapter_markdown",
        "prompt_size_limit_chars": prompt_size_limit_chars,
        "estimated_prompt_chars_without_schema": estimated_chars,
        "chunked_package_recommended": chunked,
        "ui_message": "输入包较长，建议分批复制" if chunked else "默认复制 full chapter Markdown",
        "chunked_package_mode": {
            "enabled_when_over_limit": True,
            "part_1": "chapter_context + note_anchors + correction_candidates subset",
            "part_2": "remaining correction_candidates; do not repeat chapter_markdown",
        },
    }


def _chunks_by_id(conn: Any, chunk_ids: list[Any]) -> dict[int, dict[str, Any]]:
    ids = sorted({int(value) for value in chunk_ids if _int_or_none(value) is not None})
    if not ids or not table_exists(conn, "knowledge_chunks"):
        return {}
    chunk_cols = columns(conn, "knowledge_chunks")
    selected = [
        name
        for name in [
            "id",
            "chapter_id",
            "heading_path",
            "chunk_text",
            "pdf_page_start",
            "pdf_page_end",
        ]
        if name in chunk_cols
    ]
    placeholders = ", ".join("?" for _ in ids)
    rows = conn.execute(
        f"SELECT {', '.join(selected)} FROM knowledge_chunks WHERE id IN ({placeholders})",
        ids,
    ).fetchall()
    return {int(row["id"]): dict(row) for row in rows}


def _note_payload(note: Mapping[str, Any], chunks: Mapping[int, Mapping[str, Any]], preview_chars: int) -> dict[str, Any]:
    processing = note_processing_fields(note)
    matched_chunk_id = _int_or_none(note.get("matched_chunk_id"))
    chunk = chunks.get(matched_chunk_id) if matched_chunk_id is not None else None
    warnings = _json_list(note.get("alignment_warnings_json"))
    if processing["note_processing_role"] == NOTE_ROLE_EVIDENCE_ONLY and "note_text_empty" not in warnings:
        warnings.append("note_text_empty")
    if processing["note_processing_role"] == NOTE_ROLE_USER_NOTE and not matched_chunk_id:
        warnings.append("unmatched_user_note")
    return {
        "note_id": note.get("server_note_id") or note.get("client_note_id") or str(note.get("id")),
        "server_note_id": note.get("server_note_id"),
        "client_note_id": note.get("client_note_id"),
        "source": note.get("source"),
        "zotero_annotation_key": note.get("zotero_annotation_key"),
        "zotero_item_key": note.get("zotero_item_key"),
        "zotero_attachment_key": note.get("zotero_attachment_key"),
        "page": note.get("pdf_page"),
        "page_label": note.get("page_label"),
        "selected_text_preview": _preview(note.get("selected_text"), preview_chars),
        "selected_text": note.get("selected_text") or "",
        "note_text": note.get("note_text") or "",
        "matched_document_id": note.get("matched_document_id"),
        "matched_chunk_id": matched_chunk_id,
        "matched_chunk_ids": _matched_chunk_ids(note),
        "chunk_heading_path": chunk.get("heading_path") if chunk else None,
        "chunk_page_start": chunk.get("pdf_page_start") if chunk else None,
        "chunk_page_end": chunk.get("pdf_page_end") if chunk else None,
        "chunk_evidence_text": chunk.get("chunk_text") if chunk else None,
        "evidence_alignment_status": note.get("evidence_alignment_status"),
        "alignment_confidence": note.get("alignment_confidence"),
        "alignment_method": note.get("alignment_method"),
        "warnings": list(dict.fromkeys(warnings)),
        "reviewer_warning": _reviewer_warning(note, matched_chunk_id, warnings),
        "note_processing_role": processing["note_processing_role"],
    }


def _reviewer_warning(note: Mapping[str, Any], matched_chunk_id: int | None, warnings: list[str]) -> str | None:
    if matched_chunk_id:
        return None
    if note_processing_fields(note)["note_processing_role"] != NOTE_ROLE_USER_NOTE:
        return None
    joined = ", ".join(warnings) if warnings else "matched_chunk_id_missing"
    return f"Unmatched user note; review with caution. alignment_warnings={joined}"

