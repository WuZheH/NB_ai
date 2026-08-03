"""R3-A unit note/object processing helpers.

This module is deliberately read-mostly. Production writes are left to explicit
review/apply stages; the first R3-A scripts use these helpers for dry-run
packages and temporary test fixtures.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


UNIT_PROCESSING_STATUSES = [
    "not_started",
    "notes_synced",
    "note_classification_packaged",
    "note_classification_review_needed",
    "object_prompt_packaged",
    "object_candidates_pending_review",
    "object_reviewed",
    "mechanism_ready",
    "mechanism_blocked",
]

UNIT_TYPES = {"book_chapter", "paper_section", "whole_paper_unit"}
ZOTERO_NATIVE_ANNOTATION_SOURCE = "zotero_native_annotation"
NOTE_ROLE_USER_NOTE = "user_note"
NOTE_ROLE_EVIDENCE_ONLY = "evidence_only"
NOTE_ROLE_BLOCKED = "blocked"


@dataclass(frozen=True)
class UnitRange:
    unit_type: str
    unit_id: str
    title: str
    page_start: int | None
    page_end: int | None
    chunk_ids: list[int]
    warning: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "unit_type": self.unit_type,
            "unit_id": self.unit_id,
            "title": self.title,
            "page_start": self.page_start,
            "page_end": self.page_end,
            "chunk_ids": self.chunk_ids,
            "warning": self.warning,
        }


def connect_readonly(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    uri = f"file:{path.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def columns(conn: sqlite3.Connection, table: str) -> set[str]:
    if not table_exists(conn, table):
        return set()
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any]:
    return dict(row) if row is not None else {}


def document_row(conn: sqlite3.Connection, document_id: int) -> dict[str, Any]:
    if not table_exists(conn, "documents"):
        return {}
    cols = columns(conn, "documents")
    selected = [name for name in ["id", "document_id", "title", "document_type", "object_import_mode"] if name in cols]
    if not selected:
        return {}
    id_col = "document_id" if "document_id" in cols else "id"
    row = conn.execute(
        f"SELECT {', '.join(selected)} FROM documents WHERE {id_col} = ?",
        (document_id,),
    ).fetchone()
    return row_to_dict(row)


def detect_document_units(db_path: str | Path, document_id: int) -> dict[str, Any]:
    with connect_readonly(db_path) as conn:
        doc = document_row(conn, document_id)
        if _is_book_like(doc):
            units = _book_chapter_units(conn, document_id)
            return {
                "document": doc,
                "unit_kind": "book_chapter",
                "units": [unit.to_dict() for unit in units],
                "warnings": [] if units else ["book_chapters_missing_or_empty"],
            }
        units = _paper_section_units(conn, document_id)
        warnings = [unit.warning for unit in units if unit.warning]
        return {
            "document": doc,
            "unit_kind": units[0].unit_type if units else "paper_section",
            "units": [unit.to_dict() for unit in units],
            "warnings": warnings,
        }


def find_unit_range(
    db_path: str | Path,
    document_id: int,
    unit_type: str,
    unit_id: str | None = None,
    chapter_index: int | None = None,
    section_title: str | None = None,
) -> dict[str, Any]:
    detected = detect_document_units(db_path, document_id)
    units = detected["units"]
    for unit in units:
        if unit["unit_type"] != unit_type:
            continue
        if unit_id and str(unit["unit_id"]) == str(unit_id):
            return unit
        if chapter_index is not None and str(unit["unit_id"]) in {str(chapter_index), f"chapter-{chapter_index}"}:
            return unit
        if section_title and unit["title"] == section_title:
            return unit
    if unit_type == "whole_paper_unit" and units:
        return units[0]
    return {
        "unit_type": unit_type,
        "unit_id": unit_id or section_title or chapter_index or "unknown",
        "title": section_title or "unknown unit",
        "page_start": None,
        "page_end": None,
        "chunk_ids": [],
        "warning": "unit_not_found",
    }


def document_source_keys(conn: sqlite3.Connection, document_id: int) -> dict[str, Any]:
    if not table_exists(conn, "document_sources"):
        return {}
    cols = columns(conn, "document_sources")
    selected = [
        name
        for name in ["zotero_item_key", "zotero_attachment_key", "pdf_path", "source_trace_json"]
        if name in cols
    ]
    if not selected:
        return {}
    row = conn.execute(
        f"SELECT {', '.join(selected)} FROM document_sources WHERE document_id = ? ORDER BY id LIMIT 1",
        (document_id,),
    ).fetchone()
    if not row:
        return {}
    data = dict(row)
    trace = _loads(data.get("source_trace_json"), {})
    for key in ["zotero_item_key", "zotero_attachment_key"]:
        data[key] = data.get(key) or trace.get(key)
    return data


def source_note_hash(attachment_key: str | None, selected_text: str | None, note_text: str | None) -> str:
    raw = "\n".join([attachment_key or "", selected_text or "", note_text or ""])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def note_processing_fields(note: Mapping[str, Any] | sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    selected_text = str(_note_value(note, "selected_text", "") or "").strip()
    note_text = str(_note_value(note, "note_text", "") or "").strip()
    source = str(_note_value(note, "source", "") or "")
    has_selected_text = bool(selected_text)
    has_user_note_text = bool(note_text)

    if not has_selected_text and not has_user_note_text:
        role = NOTE_ROLE_BLOCKED
    elif has_user_note_text:
        role = NOTE_ROLE_USER_NOTE
    else:
        role = NOTE_ROLE_EVIDENCE_ONLY

    is_evidence_only = role == NOTE_ROLE_EVIDENCE_ONLY
    zotero_native_kind = None
    if source == ZOTERO_NATIVE_ANNOTATION_SOURCE:
        zotero_native_kind = (
            "zotero_native_evidence_only"
            if is_evidence_only
            else "zotero_native_note"
            if role == NOTE_ROLE_USER_NOTE
            else "zotero_native_blocked"
        )

    warnings = []
    if is_evidence_only:
        warnings.append("note_text_empty")
    if role == NOTE_ROLE_BLOCKED:
        warnings.append("selected_text_and_note_text_empty")

    return {
        "note_processing_role": role,
        "is_evidence_only": is_evidence_only,
        "has_user_note_text": has_user_note_text,
        "has_selected_text": has_selected_text,
        "zotero_native_kind": zotero_native_kind,
        "note_processing_warnings": warnings,
    }


def apply_note_processing_fields(note: Mapping[str, Any] | sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    if isinstance(note, Mapping):
        data = dict(note)
    elif hasattr(note, "keys"):
        data = {key: note[key] for key in note.keys()}
    else:
        data = {}
    data.update(note_processing_fields(note))
    return data


def is_note_review_eligible(note: Mapping[str, Any] | sqlite3.Row | dict[str, Any]) -> bool:
    fields = note_processing_fields(note)
    return fields["has_user_note_text"] and fields["note_processing_role"] == NOTE_ROLE_USER_NOTE


def note_processing_summary(notes: list[Mapping[str, Any] | sqlite3.Row | dict[str, Any]]) -> dict[str, int]:
    annotated = [note_processing_fields(note) for note in notes]
    user_note_count = sum(1 for item in annotated if item["note_processing_role"] == NOTE_ROLE_USER_NOTE)
    evidence_only_count = sum(1 for item in annotated if item["is_evidence_only"])
    review_eligible_count = sum(1 for item in annotated if item["note_processing_role"] == NOTE_ROLE_USER_NOTE)
    return {
        "total_annotations": len(notes),
        "annotation_count": len(notes),
        "user_note_count": user_note_count,
        "evidence_only_count": evidence_only_count,
        "correction_review_eligible_count": review_eligible_count,
        "classification_review_eligible_count": review_eligible_count,
        "object_prompt_user_note_trigger_count": user_note_count,
        "tri_source_object_contract_status": "planned_not_implemented",
    }


def safety_flags() -> dict[str, bool]:
    return {
        "db_write_performed": False,
        "core_db_write_performed": False,
        "llm_called": False,
        "external_llm_called": False,
        "vector_store_write_performed": False,
        "mechanism_generated": False,
        "mechanism_draft_written": False,
        "zotero_db_write_performed": False,
        "seed_apply_performed": False,
        "ocr_or_marker_performed": False,
    }


def output_schema_note_classification() -> dict[str, Any]:
    return {
        "note_correction_review_candidate": {
            "misconception_check": {
                "user_note_contains_possible_misconception": False,
                "misconception_summary": "string",
            },
            "corrected_interpretation": "string",
            "ai_review_comment_for_user": "string",
            "suggested_note_revision": "string",
            "needs_user_edit": False,
        },
        "note_classification_review_candidate": {
            "note_id": "string",
            "primary_type": "memory_note|connection_note|mechanism_note|research_idea_note",
            "secondary_types": [],
            "confidence": "high|medium|low",
            "classification_rationale": "string",
            "user_tag_agreement": "agrees|disagrees|partially_agrees|no_user_type_tag",
            "corrected_user_type": "memory_note|connection_note|mechanism_note|research_idea_note|null",
            "mechanism_prompt_eligible": False,
            "reason_not_mechanism": "string",
        },
        # Backward-compatible keys for existing R3-A tests and callers.
        "note_classification": {
            "note_id": "string",
            "primary_type": "memory_note|connection_note|mechanism_note|research_idea_note",
            "secondary_types": [],
            "confidence": "high|medium|low",
            "classification_rationale": "string",
            "user_tag_agreement": "agrees|disagrees|partially_agrees|no_user_type_tag",
            "corrected_user_type": "memory_note|connection_note|mechanism_note|research_idea_note|null",
            "mechanism_prompt_eligible": False,
            "reason_not_mechanism": "string",
        },
        "misconception_check": {
            "user_note_contains_possible_misconception": False,
            "misconception_summary": "string",
            "corrected_interpretation": "string",
            "ai_review_comment_for_user": "string",
            "suggested_note_revision": "string",
            "needs_user_edit": False,
            "concept_hierarchy": [
                {
                    "concept": "string",
                    "level": "special_case|general_case|method|condition|assumption|example",
                    "explanation": "string",
                }
            ],
            "what_not_to_overgeneralize": [],
        },
    }


def output_schema_object_candidates() -> dict[str, Any]:
    return {
        "object_review_candidate": {
            "source_mode": "note_anchored|highlight_anchored|chapter_global",
            "source_origin": "legacy_note_triggered|note_sentence_required|context_supporting|section_background",
            "object_candidate_origin": "note_anchored_object|highlight_anchored_object|chapter_global_object",
            "necessity_judgment": "essential|useful|optional|probably_not_needed",
            "importance_score": "high|medium|low",
            "source_note_ids": [],
            "source_annotation_ids": [],
            "source_chunk_ids": [],
            "evidence_chunk_ids": [],
            "source_pages": [],
            "source_confidence": "high|medium|low",
            "merge_group_key": "string",
            "canonical_object_key": "string",
            "review_risk": "short string",
        },
        "tri_source_object_sources": [
            "note_anchored_object",
            "highlight_anchored_object",
            "chapter_global_object",
        ],
        "tri_source_contract_status": "planned_not_implemented",
        "relation_layer_after_object_review": {
            "relation_type": "string",
            "subject_object": "canonical_object_key",
            "target_object": "canonical_object_key",
            "relation_statement": "string",
            "evidence_notes": [],
            "evidence_highlights": [],
            "evidence_chunks": [],
            "confidence": "high|medium|low",
            "review_status": "pending|accepted|edited|rejected",
        },
        "objects": [
            {
                "object_name": "string",
                "object_type": "concept|method|algorithm|metric|model|dataset|problem|assumption|mechanism_unit",
                "source_mode": "note_anchored|highlight_anchored|chapter_global",
                "source_origin": "legacy_note_triggered|note_sentence_required|context_supporting|section_background",
                "object_candidate_origin": "note_anchored_object|highlight_anchored_object|chapter_global_object",
                "necessity_judgment": "essential|useful|optional|probably_not_needed",
                "importance_score": "high|medium|low",
                "source_note_ids": [],
                "source_annotation_ids": [],
                "source_chunk_ids": [],
                "evidence_chunk_ids": [],
                "page_labels": [],
                "source_pages": [],
                "source_confidence": "high|medium|low",
                "merge_group_key": "string",
                "canonical_object_key": "string",
                "why_candidate_exists": "short string",
                "why_it_matters": "short string",
                "review_risk": "short string",
            }
        ]
    }


def _is_book_like(doc: dict[str, Any]) -> bool:
    document_type = doc.get("document_type")
    if document_type == "book":
        return True
    if document_type == "paper":
        return False
    return doc.get("object_import_mode") == "chaptered"


def _book_chapter_units(conn: sqlite3.Connection, document_id: int) -> list[UnitRange]:
    if not table_exists(conn, "book_chapters"):
        return []
    cols = columns(conn, "book_chapters")
    selected = [name for name in ["id", "chapter_id", "chapter_index", "title", "pdf_page_start", "pdf_page_end"] if name in cols]
    if not selected:
        return []
    rows = conn.execute(
        f"SELECT {', '.join(selected)} FROM book_chapters WHERE document_id = ? ORDER BY COALESCE(chapter_index, id)",
        (document_id,),
    ).fetchall()
    chunk_map = _chunk_ids_by_chapter(conn, document_id)
    units = []
    for row in rows:
        data = dict(row)
        chapter_id = data.get("chapter_id") or data.get("id") or data.get("chapter_index")
        chapter_index = data.get("chapter_index") or chapter_id
        units.append(
            UnitRange(
                unit_type="book_chapter",
                unit_id=str(chapter_index),
                title=data.get("title") or f"Chapter {chapter_index}",
                page_start=data.get("pdf_page_start"),
                page_end=data.get("pdf_page_end"),
                chunk_ids=chunk_map.get(chapter_id, []),
            )
        )
    return units


def _paper_section_units(conn: sqlite3.Connection, document_id: int) -> list[UnitRange]:
    if not table_exists(conn, "knowledge_chunks"):
        return [
            UnitRange("whole_paper_unit", "whole_paper", "Whole paper", None, None, [], "paper_sections_unavailable_fallback_whole_paper_unit")
        ]
    cols = columns(conn, "knowledge_chunks")
    id_col = "chunk_id" if "chunk_id" in cols else "id"
    text_cols = [name for name in ["section_title", "heading_path"] if name in cols]
    page_start_col = "pdf_page_start" if "pdf_page_start" in cols else ("pdf_page" if "pdf_page" in cols else None)
    page_end_col = "pdf_page_end" if "pdf_page_end" in cols else page_start_col
    selected = [id_col, *text_cols]
    if page_start_col:
        selected.append(page_start_col)
    if page_end_col and page_end_col != page_start_col:
        selected.append(page_end_col)
    rows = conn.execute(
        f"SELECT {', '.join(selected)} FROM knowledge_chunks WHERE document_id = ? ORDER BY {id_col}",
        (document_id,),
    ).fetchall()
    sections: dict[str, dict[str, Any]] = {}
    for row in rows:
        data = dict(row)
        title = _first_level_section(data.get("section_title") or data.get("heading_path"))
        if not title:
            continue
        item = sections.setdefault(title, {"chunk_ids": [], "pages": []})
        item["chunk_ids"].append(int(data[id_col]))
        for page_col in [page_start_col, page_end_col]:
            if page_col and data.get(page_col) is not None:
                item["pages"].append(int(data[page_col]))
    if not sections:
        chunk_ids = [int(row[id_col]) for row in rows]
        return [
            UnitRange(
                "whole_paper_unit",
                "whole_paper",
                "Whole paper",
                None,
                None,
                chunk_ids,
                "paper_sections_unavailable_fallback_whole_paper_unit",
            )
        ]
    return [
        UnitRange(
            "paper_section",
            title,
            title,
            min(data["pages"]) if data["pages"] else None,
            max(data["pages"]) if data["pages"] else None,
            data["chunk_ids"],
        )
        for title, data in sections.items()
    ]


def _chunk_ids_by_chapter(conn: sqlite3.Connection, document_id: int) -> dict[Any, list[int]]:
    if not table_exists(conn, "knowledge_chunks"):
        return {}
    cols = columns(conn, "knowledge_chunks")
    id_col = "chunk_id" if "chunk_id" in cols else "id"
    if "chapter_id" not in cols:
        return {}
    rows = conn.execute(
        f"SELECT {id_col}, chapter_id FROM knowledge_chunks WHERE document_id = ? AND chapter_id IS NOT NULL",
        (document_id,),
    ).fetchall()
    result: dict[Any, list[int]] = {}
    for row in rows:
        result.setdefault(row["chapter_id"], []).append(int(row[id_col]))
    return result


def _first_level_section(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    for sep in [" / ", ">", "|"]:
        if sep in text:
            return text.split(sep)[0].strip()
    return text.strip()


def _loads(value: Any, default: Any) -> Any:
    if not isinstance(value, str) or not value.strip():
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def _note_value(note: Mapping[str, Any] | sqlite3.Row | dict[str, Any], key: str, default: Any = None) -> Any:
    if isinstance(note, Mapping):
        return note.get(key, default)
    try:
        return note[key]
    except (KeyError, IndexError, TypeError):
        return default
