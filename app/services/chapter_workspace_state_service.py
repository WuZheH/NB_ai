from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.core.paths import DEFAULT_DB_PATH
from app.services import (
    chapter_note_correction_prompt_service,
    chapter_review_pipeline_service,
    chapter_zotero_notes_dry_run_service,
)
from app.services.unit_note_object_processing_service import (
    columns,
    connect_readonly,
    note_processing_summary,
    table_exists,
)


class ChapterWorkspaceStateError(LookupError):
    pass


def build_chapter_saved_review_state(
    *,
    research_db_path: str | Path = DEFAULT_DB_PATH,
    document_id: int,
    chapter_id: int,
    review_mode: str | None = None,
    scope_id: str | None = None,
) -> dict[str, Any]:
    db_path = Path(research_db_path)
    with connect_readonly(db_path) as conn:
        chapter = _chapter_row(conn, document_id=document_id, chapter_id=chapter_id)
        if not chapter:
            raise ChapterWorkspaceStateError(
                f"chapter not found: document_id={document_id}, chapter_id={chapter_id}"
            )
        notes = _chapter_notes(conn, document_id=document_id, chapter=chapter)
    summary = note_processing_summary(notes)
    expected_items = int(summary.get("user_note_count") or 0)
    expected_sections: list[str] = []
    if expected_items:
        try:
            plan = chapter_note_correction_prompt_service.build_chapter_note_correction_review_plan(
                research_db_path=db_path,
                document_id=document_id,
                chapter_id=chapter_id,
            )
            expected_sections = [
                str(section.get("section_id"))
                for section in plan.get("sections") or []
                if section.get("section_id")
            ]
        except chapter_note_correction_prompt_service.ChapterNoteCorrectionPromptError:
            expected_sections = []
    return chapter_review_pipeline_service.build_saved_note_correction_review_state(
        research_db_path=db_path,
        document_id=document_id,
        chapter_id=chapter_id,
        expected_item_count=expected_items,
        expected_sections=expected_sections,
        review_mode=review_mode,
        scope_id=scope_id,
    )


def build_chapter_workspace_state(
    *,
    research_db_path: str | Path = DEFAULT_DB_PATH,
    zotero_db_path: str | Path = chapter_zotero_notes_dry_run_service.DEFAULT_ZOTERO_SNAPSHOT_PATH,
    document_id: int,
    chapter_id: int,
) -> dict[str, Any]:
    db_path = Path(research_db_path)
    with connect_readonly(db_path) as conn:
        document = _document_row(conn, document_id)
        if not document:
            raise ChapterWorkspaceStateError(f"document not found: {document_id}")
        chapter = _chapter_row(conn, document_id=document_id, chapter_id=chapter_id)
        if not chapter:
            raise ChapterWorkspaceStateError(
                f"chapter not found: document_id={document_id}, chapter_id={chapter_id}"
            )
        source = _document_source(conn, document_id)
        chunk_count = _chapter_chunk_count(conn, document_id=document_id, chapter_id=chapter_id)
        notes = _chapter_notes(conn, document_id=document_id, chapter=chapter)
        production_counts = {
            "object_candidates": _table_count(conn, "object_candidates"),
            "knowledge_relations": _table_count(conn, "knowledge_relations"),
            "mechanism_draft_candidates": _table_count(conn, "mechanism_draft_candidates"),
            "zotero_inspiration_notes": _table_count(conn, "zotero_inspiration_notes"),
        }
        approved_object_graph_nodes = _approved_object_graph_nodes(
            conn,
            document_id=document_id,
            chapter_id=chapter_id,
        )

    note_summary = note_processing_summary(notes)
    notes_import_status = _notes_import_status(
        research_db_path=db_path,
        zotero_db_path=Path(zotero_db_path),
        document_id=document_id,
        chapter_id=chapter_id,
        existing_summary=note_summary,
    )
    saved_review_state = build_chapter_saved_review_state(
        research_db_path=db_path,
        document_id=document_id,
        chapter_id=chapter_id,
    )
    saved_classification_state = chapter_review_pipeline_service.load_saved_note_classification_review(
        research_db_path=db_path,
        document_id=document_id,
        chapter_id=chapter_id,
    )
    classification_saved = bool(saved_classification_state)
    object_candidate_dry_run_summary = _object_candidate_dry_run_summary(
        research_db_path=db_path,
        document_id=document_id,
        chapter_id=chapter_id,
        classification_saved=classification_saved,
    )
    expected_items = int(note_summary.get("user_note_count") or 0)
    object_candidate_drafts_saved = (
        object_candidate_dry_run_summary.get("object_candidate_draft_review_status") == "pending_human_review"
        or int(object_candidate_dry_run_summary.get("object_candidate_draft_saved_count") or 0) > 0
    )
    object_candidate_human_review_saved = (
        object_candidate_dry_run_summary.get("object_candidate_human_review_status") == "saved"
        or int(object_candidate_dry_run_summary.get("object_candidate_human_review_saved_count") or 0) > 0
    )
    relation_candidate_dry_run_ready = bool(object_candidate_dry_run_summary.get("relation_candidate_package_ready"))
    no_notes_in_scope = notes_import_status["status"] == "blocked_no_notes_in_scope"
    correction_status = (
        "locked_no_notes_in_scope"
        if no_notes_in_scope
        else saved_review_state["status"]
    )
    correction_review_status = {
        "status": correction_status,
        "expected_items": expected_items,
        "saved_items": int(saved_review_state.get("saved_item_count") or 0),
        "validated_items": int(saved_review_state.get("validated_item_count") or 0),
        "partial_saved_sections": saved_review_state.get("partial_saved_sections") or [],
        "saved_sections": saved_review_state.get("source_section_ids") or saved_review_state.get("partial_saved_sections") or [],
        "missing_sections": saved_review_state.get("missing_sections") or [],
        "pn68_status": saved_review_state.get("pn68_status") or "not_saved",
        "pn68_warning_preserved": bool(saved_review_state.get("pn68_warning_preserved")),
        "pn68_reviewer_warning": saved_review_state.get("pn68_reviewer_warning") or "",
        "pn68_correction_status": saved_review_state.get("pn68_correction_status"),
        "pn68_issue_type": saved_review_state.get("pn68_issue_type"),
        "pn68_evidence_support": saved_review_state.get("pn68_evidence_support"),
        "ready_for_classification": bool(saved_review_state.get("ready_for_classification")),
        "classification_package_ready": bool(saved_review_state.get("classification_package_ready")),
        "classification_package_status": saved_review_state.get("classification_package_status") or "blocked",
        "classification_review_saved": classification_saved,
        "classification_review_status": saved_classification_state.get("status") if saved_classification_state else "not_saved",
        "classification_review_id": saved_classification_state.get("review_id") if saved_classification_state else None,
        "classification_saved_item_count": saved_classification_state.get("saved_item_count") if saved_classification_state else 0,
        "pn68_classification_label": saved_classification_state.get("pn68_classification_label") if saved_classification_state else None,
        "pn68_classification_confidence": saved_classification_state.get("pn68_confidence") if saved_classification_state else None,
    }
    save_readiness = chapter_review_pipeline_service.build_note_correction_review_save_readiness(
        research_db_path=db_path
    )
    pdf_path = (
        document.get("pdf_path")
        or source.get("source_pdf_path")
        or source.get("pdf_path")
    )
    notes_layer = "unavailable"
    if expected_items:
        notes_layer = {
            "saved": "reviewed",
            "partial": "partial_reviewed",
            "not_saved": "raw_unreviewed",
        }.get(saved_review_state["status"], "raw_unreviewed")
    studio_reason = (
        "no_notes_in_scope"
        if no_notes_in_scope
        else "correction_review_not_saved"
        if not saved_review_state.get("ready_for_classification")
        else "relation_candidate_dry_run_ready_future_phase7h_gate"
        if relation_candidate_dry_run_ready
        else "object_candidate_human_review_saved_relation_locked"
        if object_candidate_human_review_saved
        else "object_candidate_drafts_pending_human_review"
        if object_candidate_drafts_saved
        else "explicit_object_candidate_generation_gate_required"
        if classification_saved
        else "note_classification_not_completed"
    )
    flags = chapter_review_pipeline_service.review_pipeline_safety_flags()
    graph_preview = _mechanism_relation_graph_preview(
        chunk_count=chunk_count,
        note_summary=note_summary,
        object_candidate_dry_run_summary=object_candidate_dry_run_summary,
        production_counts=production_counts,
        approved_object_graph_nodes=approved_object_graph_nodes,
        flags=flags,
    )
    workspace_contract = _three_column_workspace_contract(
        source_chunked=chunk_count > 0,
        notes_layer=notes_layer,
        correction_review_status=correction_review_status,
        classification_saved=classification_saved,
        saved_classification_state=saved_classification_state,
        object_candidate_dry_run_summary=object_candidate_dry_run_summary,
        graph_preview=graph_preview,
        flags=flags,
    )
    return {
        "document": {
            "document_id": int(document["id"]),
            "title": document.get("title"),
            "zotero_item_key": source.get("zotero_item_key") or document.get("zotero_key"),
            "zotero_attachment_key": source.get("zotero_attachment_key"),
        },
        "current_chapter": {
            "chapter_id": int(chapter["id"]),
            "chapter_index": chapter.get("chapter_index"),
            "title": chapter.get("title"),
            "page_start": chapter.get("pdf_page_start"),
            "page_end": chapter.get("pdf_page_end"),
        },
        "source_ingestion_status": {
            "pdf_available": bool(pdf_path),
            "chunked": chunk_count > 0,
            "chunk_count": chunk_count,
            "zotero_source_available": bool(
                source.get("zotero_attachment_key")
                or source.get("zotero_item_key")
                or document.get("zotero_key")
            ),
        },
        "notes_import_status": notes_import_status,
        "correction_review_status": correction_review_status,
        "save_readiness": save_readiness,
        "saved_review_state": saved_review_state,
        "classification_review_status": saved_classification_state
        or {
            "status": "not_saved",
            "saved_item_count": 0,
            "ready_for_object_candidate_generation": False,
            "object_candidate_generation_status": "blocked_note_classification_not_saved",
        },
        "object_candidate_dry_run_summary": object_candidate_dry_run_summary,
        "graph_preview": graph_preview,
        "search_layer_availability": {
            "passages": "available" if chunk_count > 0 else "unavailable",
            "notes": notes_layer,
            "objects": "locked",
            "relations": "locked",
            "mechanisms": "locked",
        },
        "studio_card_states": [
            {"id": "object_graph", "status": "locked", "reason": studio_reason},
            {"id": "relation_graph", "status": "locked", "reason": studio_reason},
            {"id": "mechanism_cards", "status": "locked", "reason": studio_reason},
        ],
        "workspace_contract": workspace_contract,
        "safety_flags": flags,
    }


def _three_column_workspace_contract(
    *,
    source_chunked: bool,
    notes_layer: str,
    correction_review_status: dict[str, Any],
    classification_saved: bool,
    saved_classification_state: dict[str, Any] | None,
    object_candidate_dry_run_summary: dict[str, Any],
    graph_preview: dict[str, Any],
    flags: dict[str, Any],
) -> dict[str, Any]:
    relation_ready = bool(object_candidate_dry_run_summary.get("relation_candidate_package_ready"))
    return {
        "layout": "three_column_research_search_workspace",
        "phase": "Phase8B",
        "phase7h_entered": False,
        "left_panel": {
            "role": "full_pdf_evidence_viewer",
            "primary_source": "PDF",
            "snippet_screenshot_primary": False,
            "supports_page_jump": True,
            "supports_chunk_locator": True,
            "supports_bbox_highlight": True,
            "fallback_policy": "bbox_highlight_then_chunk_locator_then_page_jump_then_text_evidence_warning",
            "locator_contract": {
                "source_type_values": [
                    "chunk",
                    "note",
                    "inspiration_note",
                    "object_candidate",
                    "relation_candidate",
                ],
                "required_fields": ["source_type", "document_id", "pdf_page"],
                "optional_fields": [
                    "page_label",
                    "chunk_id",
                    "bbox",
                    "selected_text",
                    "highlight_label",
                ],
            },
        },
        "middle_panel": {
            "role": "research_search_structured_retrieval",
            "chat_mode": False,
            "ask_mode": False,
            "structured_result_sections": [
                "query",
                "expanded_query_preview",
                "evidence_results",
                "note_results",
                "inspiration_results",
                "object_results",
                "approved_object_candidates",
                "relation_dry_run_summary",
                "mechanism_readiness_summary",
                "safety_flags",
            ],
            "search_algorithm_phase": "SearchExp-A pending; no rerank or LLM expansion in Phase8A",
        },
        "right_panel": {
            "role": "mechanism_relation_graph_preview",
            "graph_preview": graph_preview,
            "correction_review": {
                "status": correction_review_status.get("status"),
                "saved_items": int(correction_review_status.get("saved_items") or 0),
                "expected_items": int(correction_review_status.get("expected_items") or 0),
            },
            "classification_review": {
                "status": saved_classification_state.get("status") if saved_classification_state else "not_saved",
                "saved_items": int((saved_classification_state or {}).get("saved_item_count") or 0),
                "saved": classification_saved,
            },
            "object_candidate_drafts": {
                "status": object_candidate_dry_run_summary.get("object_candidate_draft_review_status") or "not_saved",
                "saved_count": int(object_candidate_dry_run_summary.get("object_candidate_draft_saved_count") or 0),
            },
            "object_human_review": {
                "status": object_candidate_dry_run_summary.get("object_candidate_human_review_status") or "not_saved",
                "approved": int(object_candidate_dry_run_summary.get("approved_candidate_count") or 0),
                "rejected": int(object_candidate_dry_run_summary.get("rejected_candidate_count") or 0),
                "pending": int(object_candidate_dry_run_summary.get("pending_candidate_count") or 0),
            },
            "relation_dry_run": {
                "status": object_candidate_dry_run_summary.get("relation_candidate_dry_run_status") or "locked",
                "candidate_count": int(object_candidate_dry_run_summary.get("relation_candidate_count") or 0),
                "validator_valid": bool(object_candidate_dry_run_summary.get("relation_validator_valid")),
                "save_disabled": True,
                "phase7h_status": "locked_not_entered",
            },
            "mechanism": {
                "status": "locked",
                "reason": "relations_not_reviewed_phase7h" if relation_ready else "objects_or_relations_not_reviewed",
                "generated": False,
            },
            "pn68": {
                "quarantined": bool(object_candidate_dry_run_summary.get("pn68_quarantined")),
                "excluded_from_relation_dry_run": bool(object_candidate_dry_run_summary.get("pn68_excluded")),
            },
        },
        "search_layer_availability": {
            "passages": "available" if source_chunked else "unavailable",
            "notes": notes_layer,
            "relations": "planned" if relation_ready else "locked",
            "mechanisms": "locked",
        },
        "safety_flags": flags,
    }


def _mechanism_relation_graph_preview(
    *,
    chunk_count: int,
    note_summary: dict[str, Any],
    object_candidate_dry_run_summary: dict[str, Any],
    production_counts: dict[str, int],
    approved_object_graph_nodes: list[dict[str, Any]],
    flags: dict[str, Any],
) -> dict[str, Any]:
    approved_count = int(object_candidate_dry_run_summary.get("approved_candidate_count") or 0)
    relation_count = int(object_candidate_dry_run_summary.get("relation_candidate_count") or 0)
    mechanism_draft_count = int(production_counts.get("mechanism_draft_candidates") or 0)
    pn68_quarantined = bool(object_candidate_dry_run_summary.get("pn68_quarantined"))
    pn68_excluded = bool(object_candidate_dry_run_summary.get("pn68_excluded"))
    graph_nodes = [
        {
            "id": "evidence_overview",
            "type": "evidence",
            "label": "PDF evidence",
            "count": int(chunk_count or 0),
            "status": "available_read_only",
        },
        {
            "id": "note_overview",
            "type": "note",
            "label": "Zotero notes",
            "count": int(note_summary.get("annotation_count") or 0),
            "status": "reviewed_read_only",
        },
        {
            "id": "approved_objects",
            "type": "object_candidate",
            "label": "Approved object candidates",
            "count": approved_count,
            "status": "approved_human_review_read_only",
        },
        {
            "id": "relation_dry_run",
            "type": "relation_candidate",
            "label": "Relation dry-run candidates",
            "count": relation_count,
            "status": "future_phase7h_gate_required",
        },
        {
            "id": "mechanism_readiness",
            "type": "mechanism_readiness",
            "label": "Mechanism readiness",
            "count": mechanism_draft_count,
            "status": "locked_relations_not_reviewed_phase7h",
        },
        {
            "id": "pn68_quarantine",
            "type": "quarantine",
            "label": "PN68 quarantined/excluded",
            "count": 1 if pn68_quarantined else 0,
            "status": "excluded_from_relation_dry_run" if pn68_excluded else "not_excluded",
        },
    ]
    graph_nodes.extend(approved_object_graph_nodes[:6])
    graph_edges = [
        {"id": "edge_note_evidence", "source": "note_overview", "target": "evidence_overview", "type": "note_to_evidence"},
        {"id": "edge_note_object", "source": "note_overview", "target": "approved_objects", "type": "note_to_object"},
        {"id": "edge_object_relation", "source": "approved_objects", "target": "relation_dry_run", "type": "object_to_relation_dry_run"},
        {"id": "edge_relation_mechanism", "source": "relation_dry_run", "target": "mechanism_readiness", "type": "relation_to_mechanism_readiness"},
        {"id": "edge_pn68_excluded", "source": "pn68_quarantine", "target": "relation_dry_run", "type": "excluded_from_positive_relation_source"},
    ]
    graph_edges.extend(object_candidate_dry_run_summary.get("relation_preview_edges") or [])
    return {
        "status": "available_read_only",
        "role": "mechanism_relation_graph_preview",
        "source": "saved_object_candidate_human_review_and_relation_dry_run_summary",
        "node_counts": {
            "evidence_chunks": int(chunk_count or 0),
            "zotero_inspiration_notes": int(production_counts.get("zotero_inspiration_notes") or 0),
            "chapter_notes": int(note_summary.get("annotation_count") or 0),
            "approved_object_candidates": approved_count,
            "relation_dry_run_candidates": relation_count,
            "mechanism_draft_candidates": mechanism_draft_count,
            "knowledge_relations": int(production_counts.get("knowledge_relations") or 0),
            "formal_object_candidates": int(production_counts.get("object_candidates") or 0),
        },
        "nodes": graph_nodes,
        "edges": graph_edges,
        "positive_relation_sources": {
            "approved_object_candidates_only": True,
            "rejected_candidates_included": False,
            "pending_candidates_included": False,
            "pn68_included": False,
        },
        "pn68": {
            "quarantined": pn68_quarantined,
            "excluded_from_relation_dry_run": pn68_excluded,
            "positive_relation_source": False,
        },
        "phase7h_entered": False,
        "relation_rows_written": False,
        "relation_saved": False,
        "mechanism_generated": False,
        "object_registry_written": False,
        "zotero_write_performed": False,
        "vector_write_performed": False,
        "safety_flags": flags,
    }


def _object_candidate_dry_run_summary(
    *,
    research_db_path: Path,
    document_id: int,
    chapter_id: int,
    classification_saved: bool,
) -> dict[str, Any]:
    if not classification_saved:
        return {
            "ready": False,
            "status": "blocked",
            "reason": "note_classification_review_not_saved",
            "candidate_count": 0,
            "quarantined_count": 0,
            "pn68_quarantined": False,
            "object_candidate_save_status": "locked_note_classification_not_saved",
            "save_forbidden_until_phase7e_gate": True,
            "object_candidate_draft_review_status": "not_saved",
            "object_candidate_draft_saved_count": 0,
            "relation_candidate_dry_run_status": "locked_object_candidate_human_review_not_saved",
            "relation_candidate_count": 0,
            "relation_candidate_package_ready": False,
            "relation_save_status": "locked_object_candidate_human_review_not_saved",
            "relation_save_disabled": True,
            "pn68_excluded": False,
        }
    try:
        package = chapter_review_pipeline_service.build_chapter_object_candidate_dry_run_package(
            research_db_path=research_db_path,
            document_id=document_id,
            chapter_id=chapter_id,
        )
    except Exception as exc:
        return {
            "ready": False,
            "status": "unavailable",
            "reason": str(exc),
            "candidate_count": 0,
            "quarantined_count": 0,
            "pn68_quarantined": False,
            "object_candidate_save_status": "locked_dry_run_unavailable",
            "save_forbidden_until_phase7e_gate": True,
            "object_candidate_draft_review_status": "not_saved",
            "object_candidate_draft_saved_count": 0,
            "relation_candidate_dry_run_status": "unavailable",
            "relation_candidate_count": 0,
            "relation_candidate_package_ready": False,
            "relation_save_status": "locked_dry_run_unavailable",
            "relation_save_disabled": True,
            "pn68_excluded": False,
        }
    relation_summary = _relation_candidate_dry_run_summary(
        research_db_path=research_db_path,
        document_id=document_id,
        chapter_id=chapter_id,
        package=package,
    )
    return {
        "ready": bool(package.get("ready")),
        "status": package.get("status"),
        "source_classification_review_id": package.get("source_classification_review_id"),
        "source_item_count": package.get("source_item_count"),
        "label_distribution": package.get("label_distribution") or {},
        "candidate_count": package.get("candidate_count"),
        "quarantined_count": package.get("quarantined_count"),
        "pn68_quarantined": bool(package.get("pn68_quarantined")),
        "validator_valid": bool((package.get("validator_result") or {}).get("valid")),
        "object_candidate_save_status": package.get("object_candidate_save_status"),
        "save_forbidden_until_phase7e_gate": bool(package.get("save_forbidden_until_phase7e_gate", True)),
        "object_candidate_draft_review_status": package.get("object_candidate_draft_review_status") or "not_saved",
        "object_candidate_draft_review_id": package.get("object_candidate_draft_review_id"),
        "object_candidate_draft_saved_count": package.get("object_candidate_draft_saved_count") or 0,
        "saved_draft_review": package.get("saved_draft_review"),
        "object_candidate_human_review_status": package.get("object_candidate_human_review_status") or "not_saved",
        "object_candidate_human_review_id": package.get("object_candidate_human_review_id"),
        "object_candidate_human_review_saved_count": package.get("object_candidate_human_review_saved_count") or 0,
        "approved_candidate_count": package.get("approved_candidate_count") or 0,
        "rejected_candidate_count": package.get("rejected_candidate_count") or 0,
        "merged_candidate_count": package.get("merged_candidate_count") or 0,
        "pending_candidate_count": package.get("pending_candidate_count") or 0,
        "ready_for_relation_dry_run": bool(package.get("ready_for_relation_dry_run")),
        "saved_human_review": package.get("saved_human_review"),
        "relation_layer_status": package.get("relation_layer_status") or "locked_objects_not_reviewed",
        "mechanism_layer_status": package.get("mechanism_layer_status") or "locked_objects_and_relations_not_reviewed",
        "object_candidates_generated": bool(package.get("object_candidates_generated")),
        "relation_generated": bool(package.get("relation_generated")),
        "mechanism_generated": bool(package.get("mechanism_generated")),
        **relation_summary,
    }


def _relation_candidate_dry_run_summary(
    *,
    research_db_path: Path,
    document_id: int,
    chapter_id: int,
    package: dict[str, Any],
) -> dict[str, Any]:
    if not package.get("ready_for_relation_dry_run"):
        return {
            "relation_candidate_dry_run_status": package.get("relation_layer_status") or "locked_objects_not_reviewed",
            "relation_candidate_count": 0,
            "relation_candidate_package_ready": False,
            "relation_save_status": "locked_objects_not_reviewed",
            "relation_save_disabled": True,
            "pn68_excluded": False,
            "approved_source_candidate_count": 0,
            "excluded_rejected_count": 0,
            "excluded_pending_count": 0,
            "excluded_merged_count": 0,
            "pn68_source_candidate_count": 0,
            "relation_preview_edges": [],
        }
    try:
        relation_package = chapter_review_pipeline_service.build_chapter_relation_candidate_dry_run_package(
            research_db_path=research_db_path,
            document_id=document_id,
            chapter_id=chapter_id,
        )
    except Exception as exc:
        return {
            "relation_candidate_dry_run_status": "unavailable",
            "relation_candidate_count": 0,
            "relation_candidate_package_ready": False,
            "relation_save_status": "locked_relation_dry_run_unavailable",
            "relation_save_disabled": True,
            "pn68_excluded": False,
            "approved_source_candidate_count": 0,
            "excluded_rejected_count": 0,
            "excluded_pending_count": 0,
            "excluded_merged_count": 0,
            "pn68_source_candidate_count": 0,
            "relation_preview_edges": [],
            "relation_candidate_error": str(exc),
        }
    return {
        "relation_candidate_dry_run_status": relation_package.get("status") or "blocked",
        "relation_candidate_count": int(relation_package.get("relation_candidate_count") or 0),
        "relation_candidate_package_ready": bool(relation_package.get("ready")),
        "relation_save_status": relation_package.get("relation_save_status") or "future_phase7h_gate_required",
        "relation_save_disabled": bool(relation_package.get("save_relation_disabled", True)),
        "relation_source_object_candidate_human_review_id": relation_package.get("source_object_candidate_human_review_id"),
        "pn68_excluded": bool(relation_package.get("pn68_excluded")),
        "approved_source_candidate_count": int(relation_package.get("approved_source_candidate_count") or 0),
        "excluded_rejected_count": int(relation_package.get("excluded_rejected_count") or 0),
        "excluded_pending_count": int(relation_package.get("excluded_pending_count") or 0),
        "excluded_merged_count": int(relation_package.get("excluded_merged_count") or 0),
        "pn68_source_candidate_count": int(relation_package.get("pn68_source_candidate_count") or 0),
        "relation_preview_edges": _relation_preview_edges(relation_package.get("relation_candidates") or []),
        "relation_validator_valid": bool((relation_package.get("validator_result") or {}).get("valid")),
    }


def _notes_import_status(
    *,
    research_db_path: Path,
    zotero_db_path: Path,
    document_id: int,
    chapter_id: int,
    existing_summary: dict[str, Any],
) -> dict[str, Any]:
    try:
        dry_run = chapter_zotero_notes_dry_run_service.build_chapter_zotero_notes_dry_run(
            research_db_path=research_db_path,
            zotero_db_path=zotero_db_path,
            document_id=document_id,
            chapter_id=chapter_id,
        )
    except (chapter_zotero_notes_dry_run_service.ChapterZoteroNotesDryRunError, OSError):
        dry_run = {}

    existing = int(existing_summary.get("annotation_count") or 0)
    user_notes = int(existing_summary.get("user_note_count") or 0)
    evidence_only = int(existing_summary.get("evidence_only_count") or 0)
    would_insert = int(dry_run.get("would_insert_count") or 0)
    would_skip = int(dry_run.get("would_skip_existing_count") or existing)
    would_block = int(dry_run.get("would_block_count") or 0)
    no_notes = (
        dry_run.get("status") == "NO_NOTES_IN_SCOPE"
        or dry_run.get("reason") == "no_notes_in_scope"
        or (not dry_run and existing == 0)
    )
    if no_notes:
        status = "blocked_no_notes_in_scope"
        blocked_reason = "no_notes_in_scope"
    elif would_block:
        status = "blocked"
        blocked_reason = "zotero_notes_preflight_blocked"
    elif would_insert:
        status = "import_required"
        blocked_reason = None
    elif would_skip or existing:
        status = "already_imported"
        blocked_reason = None
    else:
        status = "notes_not_imported"
        blocked_reason = None
    return {
        "status": status,
        "existing": existing,
        "user_notes": user_notes,
        "evidence_only": evidence_only,
        "would_insert": would_insert,
        "would_skip_existing": would_skip,
        "would_block": would_block,
        "blocked_reason": blocked_reason,
    }


def _document_row(conn: Any, document_id: int) -> dict[str, Any]:
    if not table_exists(conn, "documents"):
        return {}
    available = columns(conn, "documents")
    selected = [
        name
        for name in ["id", "title", "zotero_key", "pdf_path"]
        if name in available
    ]
    row = conn.execute(
        f"SELECT {', '.join(selected)} FROM documents WHERE id = ?",
        (document_id,),
    ).fetchone()
    return dict(row) if row else {}


def _chapter_row(conn: Any, *, document_id: int, chapter_id: int) -> dict[str, Any]:
    if not table_exists(conn, "book_chapters"):
        return {}
    row = conn.execute(
        """
        SELECT id, chapter_index, title, pdf_page_start, pdf_page_end
        FROM book_chapters
        WHERE document_id = ? AND id = ?
        """,
        (document_id, chapter_id),
    ).fetchone()
    return dict(row) if row else {}


def _document_source(conn: Any, document_id: int) -> dict[str, Any]:
    if not table_exists(conn, "document_sources"):
        return {}
    available = columns(conn, "document_sources")
    selected = [
        name
        for name in [
            "zotero_item_key",
            "zotero_attachment_key",
            "source_trace_json",
        ]
        if name in available
    ]
    row = conn.execute(
        f"SELECT {', '.join(selected)} FROM document_sources WHERE document_id = ? ORDER BY id LIMIT 1",
        (document_id,),
    ).fetchone()
    if not row:
        return {}
    source = dict(row)
    try:
        trace = json.loads(source.get("source_trace_json") or "{}")
    except json.JSONDecodeError:
        trace = {}
    source.update({
        "zotero_item_key": source.get("zotero_item_key") or trace.get("zotero_item_key"),
        "zotero_attachment_key": source.get("zotero_attachment_key") or trace.get("zotero_attachment_key"),
        "source_pdf_path": trace.get("source_pdf_path"),
    })
    return source


def _chapter_chunk_count(conn: Any, *, document_id: int, chapter_id: int) -> int:
    if not table_exists(conn, "knowledge_chunks") or "chapter_id" not in columns(conn, "knowledge_chunks"):
        return 0
    return int(
        conn.execute(
            "SELECT COUNT(*) FROM knowledge_chunks WHERE document_id = ? AND chapter_id = ?",
            (document_id, chapter_id),
        ).fetchone()[0]
    )


def _table_count(conn: Any, table_name: str) -> int:
    if not table_exists(conn, table_name):
        return 0
    return int(conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0])


def _approved_object_graph_nodes(
    conn: Any,
    *,
    document_id: int,
    chapter_id: int,
    limit: int = 6,
) -> list[dict[str, Any]]:
    table = "object_candidate_human_review_items"
    if not table_exists(conn, table):
        return []
    available = set(columns(conn, table))
    required = {
        "candidate_temp_id",
        "document_id",
        "chapter_id",
        "approved_candidate",
        "final_object_name",
        "final_object_type",
        "source_server_note_ids_json",
        "evidence_chunk_ids_json",
    }
    if not required.issubset(available):
        return []
    rows = conn.execute(
        """
        SELECT candidate_temp_id, final_object_name, final_object_type,
               source_server_note_ids_json, evidence_chunk_ids_json
        FROM object_candidate_human_review_items
        WHERE document_id = ?
          AND chapter_id = ?
          AND approved_candidate = 1
        ORDER BY id
        LIMIT ?
        """,
        (document_id, chapter_id, max(1, min(int(limit or 6), 12))),
    ).fetchall()
    nodes: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        candidate_id = item.get("candidate_temp_id") or f"approved_object_{len(nodes) + 1}"
        nodes.append(
            {
                "id": f"object_{candidate_id}",
                "type": "object_candidate",
                "label": item.get("final_object_name") or candidate_id,
                "object_type": item.get("final_object_type") or "object_candidate",
                "candidate_temp_id": candidate_id,
                "source_server_note_ids": _json_list(item.get("source_server_note_ids_json")),
                "evidence_chunk_ids": _json_list(item.get("evidence_chunk_ids_json")),
                "status": "approved_human_review_read_only",
            }
        )
    return nodes


def _relation_preview_edges(relation_candidates: list[Any], limit: int = 8) -> list[dict[str, Any]]:
    edges: list[dict[str, Any]] = []
    for index, candidate in enumerate(relation_candidates[: max(1, min(int(limit or 8), 16))], start=1):
        if not isinstance(candidate, dict):
            continue
        relation_id = candidate.get("relation_temp_id") or candidate.get("id") or f"relation_preview_{index}"
        subject_id = candidate.get("subject_candidate_id") or candidate.get("source_candidate_id")
        object_id = candidate.get("object_candidate_id") or candidate.get("target_candidate_id")
        if not subject_id or not object_id:
            continue
        edges.append(
            {
                "id": f"edge_{relation_id}",
                "source": f"object_{subject_id}",
                "target": f"object_{object_id}",
                "type": candidate.get("relation_type") or "relation_candidate",
                "relation_temp_id": relation_id,
                "status": "dry_run_read_only",
            }
        )
    return edges


def _json_value(value: Any) -> Any:
    if value in (None, ""):
        return None
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return None


def _json_list(value: Any) -> list[Any]:
    parsed = _json_value(value)
    return parsed if isinstance(parsed, list) else []


def _chapter_notes(conn: Any, *, document_id: int, chapter: dict[str, Any]) -> list[dict[str, Any]]:
    if not table_exists(conn, "zotero_inspiration_notes"):
        return []
    available = columns(conn, "zotero_inspiration_notes")
    selected = [
        name
        for name in ["id", "source", "selected_text", "note_text", "zotero_annotation_key"]
        if name in available
    ]
    source_clause = "AND source = 'zotero_native_annotation'" if "source" in available else ""
    rows = conn.execute(
        f"""
        SELECT {', '.join(selected)}
        FROM zotero_inspiration_notes
        WHERE matched_document_id = ?
          AND pdf_page BETWEEN ? AND ?
          {source_clause}
        ORDER BY pdf_page, id
        """,
        (
            document_id,
            chapter.get("pdf_page_start"),
            chapter.get("pdf_page_end"),
        ),
    ).fetchall()
    return [dict(row) for row in rows]
