from __future__ import annotations

import json
import sqlite3
from typing import Any, Mapping

from app.services import inspiration_note_matching_service
from app.services.mechanism_source_parity_service import (
    SOURCE_BALANCE_POLICY,
    SOURCE_COVERAGE_REQUIREMENTS,
    build_mechanism_source_pack,
)


OBJECT_CANDIDATE_TABLE = "object_candidates"
INSPIRATION_TAG_SIGNALS = ("灵感", "启发", "机制", "方法", "假设", "idea", "inspiration")
APPROVED_REVIEW_STATES = {"accepted", "edited", "approved"}
PENDING_REVIEW_STATES = {"pending", "pending_review", "suggested", "candidate", "unreviewed"}
REJECTED_REVIEW_STATES = {"rejected", "deprecated"}


def build_mechanism_readiness_report(
    conn: sqlite3.Connection,
    note: Mapping[str, Any],
    match_report: Mapping[str, Any] | None = None,
    *,
    max_neighbor_chunks: int = 2,
) -> dict[str, Any]:
    match = dict(match_report) if match_report is not None else (
        inspiration_note_matching_service.build_inspiration_match_report(conn, note)
    )
    evidence = _matched_chunk_evidence(conn, match)
    linked_objects = collect_nearby_objects_for_note(
        conn,
        _optional_int(match.get("matched_document_id")),
        _optional_int(match.get("matched_chunk_id")),
        max_neighbor_chunks=max_neighbor_chunks,
    )
    linked_objects["matched_chunk_evidence"] = evidence
    warnings = [
        *list(match.get("warnings") or []),
        *list(linked_objects.get("warnings") or []),
        *_match_note_preservation_warnings(note, match),
    ]
    approved_objects = list(linked_objects["approved_objects"])
    candidate_objects = list(linked_objects["candidate_objects"])
    pending_objects = [
        item for item in candidate_objects if item["review_status"] == "pending"
    ]
    unknown_objects = [
        item for item in candidate_objects if item["review_status"] == "unknown"
    ]
    readiness_status, blockers = _readiness_decision(
        note,
        match,
        evidence,
        bool(linked_objects["object_layer_available"]),
        approved_objects,
        pending_objects,
        unknown_objects,
    )
    object_review_required = (
        not linked_objects["object_layer_available"]
        or bool(pending_objects)
        or bool(unknown_objects)
        or (
            _is_inspiration_note(note)
            and _has_bounded_evidence(note, match, evidence)
            and not approved_objects
        )
    )
    prompt_preview = build_mechanism_prompt_payload_preview(note, match, linked_objects)
    return {
        "status": "OK",
        "client_note_id": str(note.get("client_note_id") or ""),
        "server_note_id": note.get("server_note_id"),
        "readiness_status": readiness_status,
        "readiness_blockers": blockers,
        "matched_chunk_evidence": evidence,
        "linked_approved_objects": approved_objects,
        "linked_candidate_objects": candidate_objects,
        "object_review_required": object_review_required,
        "mechanism_prompt_payload_preview": prompt_preview,
        "evidence_completeness_score": _evidence_completeness_score(note, match, evidence),
        "selected_text_preserved": True,
        "note_text_preserved": True,
        "user_tags_preserved": True,
        "warnings": list(dict.fromkeys(warnings)),
        "db_write_performed": False,
        "mechanism_generated": False,
        "llm_called": False,
        "knowledge_chunks_write_performed": False,
        "lancedb_write_performed": False,
    }


def build_mechanism_readiness_batch(
    conn: sqlite3.Connection,
    notes: list[Mapping[str, Any]],
    *,
    match_reports: list[Mapping[str, Any]] | None = None,
    max_neighbor_chunks: int = 2,
) -> dict[str, Any]:
    report_by_note_id = {
        str(report.get("client_note_id") or ""): report for report in (match_reports or [])
    }
    reports = [
        build_mechanism_readiness_report(
            conn,
            note,
            report_by_note_id.get(str(note.get("client_note_id") or "")),
            max_neighbor_chunks=max_neighbor_chunks,
        )
        for note in notes
    ]
    return {
        "status": "OK",
        "reports": reports,
        "count": len(reports),
        "db_write_performed": False,
        "mechanism_generated": False,
        "llm_called": False,
        "knowledge_chunks_write_performed": False,
        "lancedb_write_performed": False,
    }


def collect_nearby_objects_for_note(
    conn: sqlite3.Connection,
    matched_document_id: int | None,
    matched_chunk_id: int | None,
    *,
    max_neighbor_chunks: int = 2,
) -> dict[str, Any]:
    if not _table_exists(conn, OBJECT_CANDIDATE_TABLE):
        return _object_collection_missing("object_candidates_table_unavailable")
    columns = _columns(conn, OBJECT_CANDIDATE_TABLE)
    required = {"id", "object_name", "object_type", "review_status", "mapped_chunk_ids_json"}
    if required - columns:
        return _object_collection_missing("object_candidates_schema_not_readable")
    if matched_chunk_id is None:
        return {
            "object_layer_available": True,
            "approved_objects": [],
            "candidate_objects": [],
            "warnings": [],
        }

    anchors = _linked_chunk_anchors(
        conn,
        matched_document_id,
        matched_chunk_id,
        max_neighbor_chunks=max_neighbor_chunks,
    )
    selected_columns = [
        "id",
        "object_name",
        "object_type",
        "review_status",
        "mapped_chunk_ids_json",
    ]
    for optional in ("object_key", "status", "document_id", "evidence_refs_json"):
        if optional in columns:
            selected_columns.append(optional)
    rows = _query_dicts(
        conn,
        f"SELECT {', '.join(selected_columns)} FROM {OBJECT_CANDIDATE_TABLE}",
    )
    approved_objects: list[dict[str, Any]] = []
    candidate_objects: list[dict[str, Any]] = []
    for row in rows:
        if str(row.get("status") or "").strip().lower() == "deprecated":
            continue
        chunk_ids = _row_evidence_chunk_ids(row)
        link_reason = _best_link_reason(chunk_ids, anchors)
        if link_reason is None:
            continue
        summary = {
            "object_id": row["id"],
            "object_key": row.get("object_key"),
            "object_name": str(row.get("object_name") or ""),
            "object_type": str(row.get("object_type") or "unknown"),
            "review_status": _public_review_status(row.get("review_status")),
            "source_review_status": (
                None if row.get("review_status") is None else str(row["review_status"])
            ),
            "evidence_chunk_ids": chunk_ids,
            "link_reason": link_reason,
        }
        if summary["review_status"] == "approved":
            approved_objects.append(summary)
        else:
            candidate_objects.append(summary)
    return {
        "object_layer_available": True,
        "approved_objects": approved_objects,
        "candidate_objects": candidate_objects,
        "warnings": [],
    }


def build_mechanism_prompt_payload_preview(
    note: Mapping[str, Any],
    match_report: Mapping[str, Any],
    linked_objects: Mapping[str, Any],
) -> dict[str, Any]:
    evidence = linked_objects.get("matched_chunk_evidence") or {}
    approved_objects = list(linked_objects.get("approved_objects") or [])
    candidate_objects = list(linked_objects.get("candidate_objects") or [])
    source_note_id = note.get("server_note_id") or note.get("client_note_id")
    chunk_id = evidence.get("chunk_id") or match_report.get("matched_chunk_id")
    nearby_context = _nearby_context(note)
    source_note = {
        "inspiration_note_id": source_note_id,
        "user_note_text": note.get("note_text"),
        "selected_text": note.get("selected_text"),
        "user_tags": list(note.get("user_tags") or []),
        "selection_type": note.get("selection_type"),
        "zotero_attachment_key": note.get("zotero_attachment_key"),
        "zotero_annotation_key": note.get("zotero_annotation_key"),
        "pdf_page": note.get("pdf_page"),
        "page_label": note.get("page_label"),
    }
    source_evidence = {
        "chunk_id": chunk_id,
        "chunk_text": evidence.get("chunk_text"),
        "nearby_context": nearby_context,
        "document_id": evidence.get("document_id") or match_report.get("matched_document_id"),
        "document_title": evidence.get("document_title"),
        "chapter_id": evidence.get("chapter_id"),
        "chapter_title": evidence.get("chapter_title"),
        "pdf_page": evidence.get("pdf_page") or match_report.get("matched_pdf_page"),
        "source_trace": {
            "match_method": match_report.get("match_method"),
            "confidence": match_report.get("match_confidence"),
        },
    }
    matched_chunks = []
    if chunk_id is not None:
        matched_chunks.append(source_evidence)
    mechanism_source_pack = build_mechanism_source_pack(
        note=note,
        source_note_id=source_note_id,
        source_excerpt=source_evidence,
        matched_chunks=matched_chunks,
        nearby_chunks=(
            [{"nearby_context": nearby_context, "role": "context_support"}]
            if nearby_context
            else []
        ),
        linked_objects=approved_objects,
    )
    pending_ids = [
        item["object_id"]
        for item in candidate_objects
        if item["review_status"] in {"pending", "unknown"}
    ]
    object_layer_available = bool(linked_objects.get("object_layer_available"))
    return {
        "schema_version": "mechanism_prompt_input_v1",
        "generation_mode": "draft_only",
        "user_note_text": note.get("note_text"),
        "selected_text": note.get("selected_text"),
        "chunk_text": evidence.get("chunk_text"),
        "nearby_context": nearby_context,
        "document_title": evidence.get("document_title"),
        "chapter_id": evidence.get("chapter_id"),
        "chapter_title": evidence.get("chapter_title"),
        "pdf_page": evidence.get("pdf_page") or match_report.get("matched_pdf_page"),
        "approved_objects": approved_objects,
        "candidate_objects": candidate_objects,
        "mechanism_source_pack": mechanism_source_pack,
        "source_mode": mechanism_source_pack["source_mode"],
        "primary_user_note": mechanism_source_pack["primary_user_note"],
        "primary_source_excerpt": mechanism_source_pack["primary_source_excerpt"],
        "source_balance_policy": dict(SOURCE_BALANCE_POLICY),
        "source_coverage_requirements": list(SOURCE_COVERAGE_REQUIREMENTS),
        "source_inspiration_note_id": source_note_id,
        "evidence_chunk_ids": [chunk_id] if chunk_id is not None else [],
        "constraints": [
            "do_not_overwrite_user_note",
            "treat_user_note_and_source_excerpt_as_equal_primary_sources",
            "evidence_bound_only",
            "do_not_force_note_to_dominate_source_excerpt",
            "do_not_reduce_source_excerpt_to_citation_only",
            "do_not_claim_validated_results",
        ],
        "source_inspiration_notes": [source_note],
        "evidence": [source_evidence] if chunk_id is not None else [],
        "object_review_state": {
            "required_object_ids": [item["object_id"] for item in approved_objects + candidate_objects],
            "pending_object_ids": pending_ids,
            "ready_for_mechanism_review": (
                object_layer_available and bool(approved_objects) and not pending_ids
            ),
        },
        "user_research_context": None,
        "previous_mechanisms": [],
    }


def _readiness_decision(
    note: Mapping[str, Any],
    match: Mapping[str, Any],
    evidence: Mapping[str, Any] | None,
    object_layer_available: bool,
    approved_objects: list[dict[str, Any]],
    pending_objects: list[dict[str, Any]],
    unknown_objects: list[dict[str, Any]],
) -> tuple[str, list[str]]:
    if not _is_inspiration_note(note):
        return "not_inspiration_note", ["note_has_no_inspiration_signal"]
    if match.get("match_method") == "unmatched":
        return "blocked_by_unmatched_note", ["source_evidence_unmatched"]
    if not _has_bounded_evidence(note, match, evidence):
        return "blocked_by_missing_evidence", ["matched_chunk_evidence_required"]
    if match.get("match_confidence") == "low":
        return "blocked_by_low_confidence_match", ["low_confidence_match_requires_review"]
    if not object_layer_available:
        return "missing_object_layer", ["run_object_candidate_extraction_and_review_first"]
    if pending_objects:
        return "blocked_by_object_review", ["pending_linked_object_review"]
    if unknown_objects:
        return "needs_manual_review", ["linked_object_review_state_unknown"]
    if not approved_objects:
        return "needs_manual_review", ["no_linked_approved_object_review_record"]
    return "ready_for_mechanism_prompt", []


def _is_inspiration_note(note: Mapping[str, Any]) -> bool:
    if str(note.get("note_text") or "").strip():
        return True
    tags = [str(item).strip().casefold() for item in note.get("user_tags") or []]
    return any(signal.casefold() in tag for tag in tags for signal in INSPIRATION_TAG_SIGNALS)


def _has_bounded_evidence(
    note: Mapping[str, Any],
    match: Mapping[str, Any],
    evidence: Mapping[str, Any] | None,
) -> bool:
    if match.get("matched_chunk_id") is None:
        return False
    selected_text_present = bool(str(note.get("selected_text") or "").strip())
    chunk_text_present = bool(str((evidence or {}).get("chunk_text") or "").strip())
    return selected_text_present or chunk_text_present


def _evidence_completeness_score(
    note: Mapping[str, Any],
    match: Mapping[str, Any],
    evidence: Mapping[str, Any] | None,
) -> float:
    score = 0.0
    if str(note.get("selected_text") or "").strip():
        score += 0.25
    if match.get("matched_chunk_id") is not None:
        score += 0.30
    if str((evidence or {}).get("chunk_text") or "").strip():
        score += 0.20
    if match.get("match_confidence") in {"high", "medium"}:
        score += 0.15
    if note.get("zotero_annotation_key") or note.get("pdf_page") or match.get("matched_pdf_page"):
        score += 0.10
    return round(min(score, 1.0), 2)


def _matched_chunk_evidence(
    conn: sqlite3.Connection,
    match_report: Mapping[str, Any],
) -> dict[str, Any] | None:
    chunk_id = _optional_int(match_report.get("matched_chunk_id"))
    if chunk_id is None:
        return None
    if not _table_exists(conn, "knowledge_chunks"):
        return _fallback_evidence_from_match(match_report, chunk_id)
    columns = _columns(conn, "knowledge_chunks")
    required = {"id", "document_id", "chunk_text"}
    if required - columns:
        return _fallback_evidence_from_match(match_report, chunk_id)
    selected = ["id AS chunk_id", "document_id", "chunk_text"]
    selected.append("pdf_page_start AS pdf_page" if "pdf_page_start" in columns else "NULL AS pdf_page")
    selected.append("heading_path AS chapter_title" if "heading_path" in columns else "NULL AS chapter_title")
    row = _query_one_dict(
        conn,
        f"SELECT {', '.join(selected)} FROM knowledge_chunks WHERE id = ?",
        (chunk_id,),
    )
    if row is None:
        return _fallback_evidence_from_match(match_report, chunk_id)
    row["document_title"] = _document_title(conn, _optional_int(row.get("document_id")))
    row["source_trace"] = {
        "match_method": match_report.get("match_method"),
        "confidence": match_report.get("match_confidence"),
    }
    return row


def _fallback_evidence_from_match(
    match_report: Mapping[str, Any],
    chunk_id: int,
) -> dict[str, Any]:
    context = match_report.get("evidence_context") or {}
    return {
        "chunk_id": chunk_id,
        "document_id": match_report.get("matched_document_id"),
        "chunk_text": context.get("chunk_text"),
        "pdf_page": match_report.get("matched_pdf_page"),
        "chapter_title": None,
        "document_title": None,
        "source_trace": {
            "match_method": match_report.get("match_method"),
            "confidence": match_report.get("match_confidence"),
        },
    }


def _document_title(conn: sqlite3.Connection, document_id: int | None) -> str | None:
    if document_id is None or not _table_exists(conn, "documents"):
        return None
    if not {"id", "title"}.issubset(_columns(conn, "documents")):
        return None
    row = conn.execute("SELECT title FROM documents WHERE id = ?", (document_id,)).fetchone()
    return None if row is None else str(row[0])


def _linked_chunk_anchors(
    conn: sqlite3.Connection,
    document_id: int | None,
    chunk_id: int,
    *,
    max_neighbor_chunks: int,
) -> dict[int, str]:
    anchors = {chunk_id: "same_chunk"}
    if max_neighbor_chunks <= 0 or document_id is None or not _table_exists(conn, "knowledge_chunks"):
        return anchors
    columns = _columns(conn, "knowledge_chunks")
    if not {"id", "document_id"}.issubset(columns):
        return anchors
    target = _query_one_dict(
        conn,
        "SELECT id, pdf_page_start FROM knowledge_chunks WHERE id = ?"
        if "pdf_page_start" in columns
        else "SELECT id, NULL AS pdf_page_start FROM knowledge_chunks WHERE id = ?",
        (chunk_id,),
    )
    if target and target.get("pdf_page_start") is not None:
        rows = _query_dicts(
            conn,
            """
            SELECT id FROM knowledge_chunks
            WHERE document_id = ? AND pdf_page_start = ? AND id != ?
            ORDER BY id LIMIT ?
            """,
            (document_id, target["pdf_page_start"], chunk_id, max_neighbor_chunks),
        )
        for row in rows:
            anchors[int(row["id"])] = "same_page"
    remaining = max_neighbor_chunks - (len(anchors) - 1)
    if remaining > 0:
        rows = _query_dicts(
            conn,
            """
            SELECT id FROM knowledge_chunks
            WHERE document_id = ? AND id != ?
            ORDER BY ABS(id - ?) LIMIT ?
            """,
            (document_id, chunk_id, chunk_id, remaining),
        )
        for row in rows:
            anchors.setdefault(int(row["id"]), "nearby_chunk")
    return anchors


def _best_link_reason(chunk_ids: list[int], anchors: Mapping[int, str]) -> str | None:
    priority = {"same_chunk": 0, "same_page": 1, "nearby_chunk": 2, "tag_relation": 3}
    reasons = [anchors[item] for item in chunk_ids if item in anchors]
    return min(reasons, key=lambda value: priority[value]) if reasons else None


def _row_evidence_chunk_ids(row: Mapping[str, Any]) -> list[int]:
    values = _json_list(row.get("mapped_chunk_ids_json"))
    chunk_ids = [_optional_int(value) for value in values]
    filtered = [value for value in chunk_ids if value is not None]
    if filtered:
        return list(dict.fromkeys(filtered))
    refs = _json_list(row.get("evidence_refs_json"))
    return list(
        dict.fromkeys(
            value
            for value in (
                _optional_int(item.get("chunk_id")) for item in refs if isinstance(item, Mapping)
            )
            if value is not None
        )
    )


def _public_review_status(value: Any) -> str:
    status = str(value or "").strip().lower()
    if status in APPROVED_REVIEW_STATES:
        return "approved"
    if status in PENDING_REVIEW_STATES:
        return "pending"
    if status in REJECTED_REVIEW_STATES:
        return "rejected"
    return "unknown"


def _nearby_context(note: Mapping[str, Any]) -> str | None:
    parts = [
        str(value)
        for value in (note.get("context_before"), note.get("context_after"))
        if value is not None and str(value) != ""
    ]
    return "\n".join(parts) if parts else None


def _match_note_preservation_warnings(
    note: Mapping[str, Any],
    match_report: Mapping[str, Any],
) -> list[str]:
    raw_note = match_report.get("raw_note")
    if not isinstance(raw_note, Mapping):
        return []
    for field in ("selected_text", "note_text", "user_tags"):
        if raw_note.get(field) != note.get(field):
            return ["match_report_raw_note_mismatch_preview_uses_request_note"]
    return []


def _object_collection_missing(reason: str) -> dict[str, Any]:
    return {
        "object_layer_available": False,
        "approved_objects": [],
        "candidate_objects": [],
        "warnings": [reason],
    }


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _query_dicts(
    conn: sqlite3.Connection,
    statement: str,
    parameters: tuple[Any, ...] = (),
) -> list[dict[str, Any]]:
    cursor = conn.execute(statement, parameters)
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def _query_one_dict(
    conn: sqlite3.Connection,
    statement: str,
    parameters: tuple[Any, ...] = (),
) -> dict[str, Any] | None:
    rows = _query_dicts(conn, statement, parameters)
    return rows[0] if rows else None


def _json_list(value: Any) -> list[Any]:
    try:
        loaded = json.loads(str(value or "[]"))
    except (TypeError, ValueError):
        return []
    return loaded if isinstance(loaded, list) else []


def _optional_int(value: Any) -> int | None:
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None
