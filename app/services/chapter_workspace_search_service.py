from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.core.paths import DEFAULT_DB_PATH
from app.services import chapter_workspace_state_service
from app.services.unit_note_object_processing_service import columns, connect_readonly, table_exists


MAX_QUERY_TERMS = 12
DEFAULT_LIMIT_PER_LAYER = 6
SNIPPET_CHARS = 260

QUERY_EXPANSIONS = {
    "层数": ("layer", "layers", "depth", "deeper"),
    "增加": ("increase", "increasing", "larger", "deep"),
    "梯度": ("gradient", "gradients"),
    "消失": ("vanish", "vanishing"),
    "爆炸": ("explod", "exploding"),
    "解决": ("solution", "solve", "avoid", "prevent", "stability"),
    "办法": ("solution", "method", "approach"),
    "牛顿": ("newton",),
    "优化": ("optimization", "optimize"),
}


class ChapterWorkspaceSearchError(LookupError):
    pass


def build_chapter_workspace_search(
    *,
    research_db_path: str | Path = DEFAULT_DB_PATH,
    document_id: int,
    chapter_id: int,
    query: str,
    limit_per_layer: int = DEFAULT_LIMIT_PER_LAYER,
) -> dict[str, Any]:
    db_path = Path(research_db_path)
    clean_query = " ".join(str(query or "").split())
    limit = max(1, min(int(limit_per_layer or DEFAULT_LIMIT_PER_LAYER), 20))
    try:
        workspace = chapter_workspace_state_service.build_chapter_workspace_state(
            research_db_path=db_path,
            document_id=document_id,
            chapter_id=chapter_id,
        )
    except chapter_workspace_state_service.ChapterWorkspaceStateError as exc:
        raise ChapterWorkspaceSearchError(str(exc)) from exc

    terms = _query_terms(clean_query)
    with connect_readonly(db_path) as conn:
        chapter = _chapter_row(conn, document_id=document_id, chapter_id=chapter_id)
        if not chapter:
            raise ChapterWorkspaceSearchError(
                f"chapter not found: document_id={document_id}, chapter_id={chapter_id}"
            )
        document = workspace.get("document") or {}
        passage_results = _passage_results(
            conn,
            document=document,
            chapter=chapter,
            terms=terms,
            limit=limit,
        )
        note_results = _note_results(
            conn,
            document=document,
            chapter=chapter,
            terms=terms,
            limit=limit,
        )
        object_existing_count = _object_candidate_count(conn, document_id=document_id, chapter_id=chapter_id)
        mechanism_existing_count = _mechanism_draft_count(conn, document_id=document_id)
        approved_object_candidates = _approved_object_candidates(
            conn,
            document_id=document_id,
            chapter_id=chapter_id,
        )

    no_notes = workspace.get("notes_import_status", {}).get("status") == "blocked_no_notes_in_scope"
    source_status = workspace.get("source_ingestion_status") or {}
    notes_status = workspace.get("search_layer_availability", {}).get("notes") or "unavailable"
    object_summary = workspace.get("object_candidate_dry_run_summary") or {}
    object_human_review_saved = (
        object_summary.get("object_candidate_human_review_status") == "saved"
        or int(object_summary.get("object_candidate_human_review_saved_count") or 0) > 0
    )
    relation_dry_run_ready = (
        object_summary.get("relation_candidate_package_ready") is True
        or object_summary.get("relation_candidate_dry_run_status") == "relation_candidate_dry_run_ready"
        or int(object_summary.get("relation_candidate_count") or 0) > 0
    )
    safety = _safety_flags()
    relation_dry_run_summary = {
        "status": "ready" if relation_dry_run_ready else "locked",
        "reason": "relation_candidate_dry_run_ready_future_phase7h_gate"
        if relation_dry_run_ready
        else "objects_not_reviewed",
        "candidate_count": int(object_summary.get("relation_candidate_count") or 0),
        "approved_source_candidate_count": int(object_summary.get("approved_candidate_count") or 0),
        "validator_valid": bool(object_summary.get("relation_validator_valid")),
        "pn68_excluded": bool(object_summary.get("pn68_excluded")),
        "phase7h_status": "locked_not_entered",
        "relation_save_disabled": True,
        "relation_generated": False,
    }
    mechanism_readiness_summary = {
        "status": "locked",
        "reason": "relations_not_reviewed_phase7h"
        if relation_dry_run_ready
        else "objects_or_relations_not_reviewed",
        "existing_draft_count": mechanism_existing_count,
        "phase7h_required": True,
        "mechanism_generated": False,
    }
    structured = {
        "query": clean_query,
        "expanded_query_preview": None,
        "evidence_results": passage_results if source_status.get("chunked") else [],
        "note_results": [] if no_notes else note_results,
        "inspiration_results": [],
        "object_results": [],
        "approved_object_candidates": approved_object_candidates,
        "relation_dry_run_summary": relation_dry_run_summary,
        "mechanism_readiness_summary": mechanism_readiness_summary,
        "locator_contract": _locator_contract_definition(),
        "safety_flags": safety,
    }
    return {
        "query": clean_query,
        "document_id": document_id,
        "chapter_id": chapter_id,
        "mode": "workspace_search_read_only_v1",
        "workspace_search_role": "research_search_structured_retrieval",
        "expanded_query_preview": None,
        "evidence_results": structured["evidence_results"],
        "note_results": structured["note_results"],
        "inspiration_results": structured["inspiration_results"],
        "object_results": structured["object_results"],
        "approved_object_candidates": approved_object_candidates,
        "relation_dry_run_summary": relation_dry_run_summary,
        "mechanism_readiness_summary": mechanism_readiness_summary,
        "locator_contract": structured["locator_contract"],
        "structured_retrieval_result": structured,
        "layers": {
            "passage_results_with_pdf_preview": {
                "status": "available" if source_status.get("chunked") else "unavailable",
                "reason": "available from existing knowledge_chunks" if source_status.get("chunked") else "requires chunked PDF",
                "results": passage_results if source_status.get("chunked") else [],
                "no_direct_match": bool(source_status.get("chunked") and clean_query and not passage_results),
            },
            "note_results_with_pdf_preview": {
                "status": "unavailable" if no_notes else notes_status,
                "reason": "No Zotero notes in this chapter" if no_notes else "review not saved",
                "results": [] if no_notes else note_results,
                "no_direct_match": bool((not no_notes) and clean_query and not note_results),
            },
            "object_results": {
                "status": "locked",
                "reason": "object_candidate_human_review_saved_relation_locked" if object_human_review_saved else "correction_review_not_saved",
                "existing_candidate_count": object_existing_count,
                "approved_candidate_count": int(object_summary.get("approved_candidate_count") or 0),
                "results": [],
            },
            "relation_results": {
                "status": "planned" if relation_dry_run_ready else "locked",
                "reason": "relation_candidate_dry_run_ready_future_phase7h_gate" if relation_dry_run_ready else "objects_not_reviewed",
                "dry_run_candidate_count": int(object_summary.get("relation_candidate_count") or 0),
                "approved_source_candidate_count": int(object_summary.get("approved_candidate_count") or 0),
                "pn68_excluded": bool(object_summary.get("pn68_excluded")),
                "results": [],
            },
            "insight_or_mechanism_results": {
                "status": "locked",
                "reason": "relations_not_reviewed_phase7h" if relation_dry_run_ready else "objects_or_relations_not_reviewed",
                "existing_draft_count": mechanism_existing_count,
                "results": [],
            },
        },
        "workspace_state_summary": {
            "notes": workspace.get("search_layer_availability", {}).get("notes"),
            "objects": workspace.get("search_layer_availability", {}).get("objects"),
            "relations": workspace.get("search_layer_availability", {}).get("relations"),
            "mechanisms": workspace.get("search_layer_availability", {}).get("mechanisms"),
            "production_review_write_allowed": workspace.get("save_readiness", {}).get("production_review_write_allowed"),
            "current_blockers": workspace.get("save_readiness", {}).get("current_blockers") or [],
        },
        "safety_flags": safety,
        **safety,
    }


def _passage_results(
    conn: Any,
    *,
    document: dict[str, Any],
    chapter: dict[str, Any],
    terms: list[str],
    limit: int,
) -> list[dict[str, Any]]:
    if not table_exists(conn, "knowledge_chunks"):
        return []
    rows = conn.execute(
        """
        SELECT id, document_id, chapter_id, heading_path, chunk_text, pdf_page_start, pdf_page_end
        FROM knowledge_chunks
        WHERE document_id = ? AND chapter_id = ?
        ORDER BY chunk_index, id
        """,
        (document.get("document_id"), chapter.get("id")),
    ).fetchall()
    ranked = _rank_rows([dict(row) for row in rows], terms, ("heading_path", "chunk_text"))
    return [
        _passage_result(row, document=document, chapter=chapter, terms=terms)
        for row in ranked[:limit]
        if _score_row(row, terms, ("heading_path", "chunk_text")) > 0
    ]


def _note_results(
    conn: Any,
    *,
    document: dict[str, Any],
    chapter: dict[str, Any],
    terms: list[str],
    limit: int,
) -> list[dict[str, Any]]:
    if not table_exists(conn, "zotero_inspiration_notes"):
        return []
    page_start = chapter.get("pdf_page_start") or 0
    page_end = chapter.get("pdf_page_end") or page_start
    note_cols = set(columns(conn, "zotero_inspiration_notes"))
    select_fields = [
        "n.id",
        "n.server_note_id",
        "n.client_note_id",
        "n.source",
        "n.zotero_annotation_key",
        "n.pdf_page",
        "n.page_label",
        "n.selected_text",
        "n.note_text",
        "n.bbox_json",
        "n.matched_document_id",
        "n.matched_chunk_id",
        "n.matched_chunk_ids_json",
        "n.evidence_alignment_status",
        "n.alignment_confidence",
        "n.alignment_method",
        "n.alignment_warnings_json",
    ]
    if "matched_document_id" not in note_cols:
        return []
    rows = conn.execute(
        f"""
        SELECT {', '.join(select_fields)},
               k.heading_path AS chunk_heading_path,
               k.chunk_text AS chunk_evidence_text,
               k.pdf_page_start AS chunk_page_start
        FROM zotero_inspiration_notes n
        LEFT JOIN knowledge_chunks k ON k.id = n.matched_chunk_id
        WHERE n.matched_document_id = ?
          AND n.pdf_page BETWEEN ? AND ?
          AND n.source = 'zotero_native_annotation'
        ORDER BY n.pdf_page, n.id
        """,
        (document.get("document_id"), page_start, page_end),
    ).fetchall()
    ranked = _rank_rows(
        [dict(row) for row in rows],
        terms,
        ("note_text", "selected_text", "chunk_evidence_text", "zotero_annotation_key", "server_note_id", "client_note_id"),
    )
    return [
        _note_result(row, document=document, chapter=chapter, terms=terms)
        for row in ranked[:limit]
        if _score_row(
            row,
            terms,
            ("note_text", "selected_text", "chunk_evidence_text", "zotero_annotation_key", "server_note_id", "client_note_id"),
        ) > 0
    ]


def _passage_result(row: dict[str, Any], *, document: dict[str, Any], chapter: dict[str, Any], terms: list[str]) -> dict[str, Any]:
    page = row.get("pdf_page_start")
    heading = str(row.get("heading_path") or chapter.get("title") or "")
    chunk_text = str(row.get("chunk_text") or "")
    return {
        "id": f"passage-{row.get('id')}",
        "source_kind": "passage",
        "source_type": "chunk",
        "title": _heading_title(heading) or f"Passage chunk {row.get('id')}",
        "snippet": _snippet(chunk_text, terms),
        "page": page,
        "page_label": f"p.{page}" if page else "",
        "chunk_id": row.get("id"),
        "heading_path": heading,
        "source_trace": {
            "source_kind": "passage",
            "source_type": "chunk",
            "document_id": document.get("document_id"),
            "chapter_id": chapter.get("id"),
            "chunk_id": row.get("id"),
            "pdf_page": page,
        },
        "locator": _source_locator(
            source_type="chunk",
            document=document,
            chapter=chapter,
            page=page,
            page_label=f"p.{page}" if page else "",
            chunk_id=row.get("id"),
            bbox=None,
            selected_text="",
            highlight_label=_heading_title(heading) or f"chunk {row.get('id')}",
        ),
        "source_target": _source_target(
            source_kind="passage",
            document=document,
            chapter=chapter,
            page=page,
            page_label=f"p.{page}" if page else "",
            selected_text="",
            note_text="",
            chunk_evidence_text=chunk_text,
            matched_chunk_id=row.get("id"),
            chunk_heading_path=heading,
            developer_meta={"source": "workspace_search", "result_layer": "passage_results_with_pdf_preview"},
        ),
    }


def _note_result(row: dict[str, Any], *, document: dict[str, Any], chapter: dict[str, Any], terms: list[str]) -> dict[str, Any]:
    page = row.get("pdf_page") or row.get("chunk_page_start")
    note_text = str(row.get("note_text") or "")
    selected_text = str(row.get("selected_text") or "")
    chunk_text = str(row.get("chunk_evidence_text") or "")
    warnings = _json_list(row.get("alignment_warnings_json"))
    return {
        "id": f"note-{row.get('id')}",
        "source_kind": "note",
        "source_type": "note",
        "title": note_text[:80] or row.get("server_note_id") or row.get("client_note_id") or "Zotero note",
        "snippet": _snippet(" ".join(part for part in [note_text, selected_text, chunk_text] if part), terms),
        "page": page,
        "page_label": row.get("page_label") or (f"p.{page}" if page else ""),
        "chunk_id": row.get("matched_chunk_id"),
        "note_id": row.get("server_note_id") or row.get("client_note_id"),
        "selected_text": selected_text,
        "note_text": note_text,
        "chunk_evidence_text": chunk_text,
        "server_note_id": row.get("server_note_id"),
        "client_note_id": row.get("client_note_id"),
        "zotero_annotation_key": row.get("zotero_annotation_key"),
        "review_badge": "review not saved",
        "source_trace": {
            "source_kind": "note",
            "source_type": "note",
            "document_id": document.get("document_id"),
            "chapter_id": chapter.get("id"),
            "chunk_id": row.get("matched_chunk_id"),
            "pdf_page": page,
            "server_note_id": row.get("server_note_id"),
            "client_note_id": row.get("client_note_id"),
            "zotero_annotation_key": row.get("zotero_annotation_key"),
        },
        "locator": _source_locator(
            source_type="note",
            document=document,
            chapter=chapter,
            page=page,
            page_label=row.get("page_label") or (f"p.{page}" if page else ""),
            chunk_id=row.get("matched_chunk_id"),
            bbox=_json_value(row.get("bbox_json")),
            selected_text=selected_text,
            highlight_label=note_text[:80] or row.get("server_note_id") or "Zotero note",
            server_note_id=row.get("server_note_id") or "",
            client_note_id=row.get("client_note_id") or "",
            zotero_annotation_key=row.get("zotero_annotation_key") or "",
        ),
        "source_target": _source_target(
            source_kind="note",
            document=document,
            chapter=chapter,
            page=page,
            page_label=row.get("page_label") or (f"p.{page}" if page else ""),
            selected_text=selected_text,
            note_text=note_text,
            chunk_evidence_text=chunk_text,
            matched_chunk_id=row.get("matched_chunk_id"),
            chunk_heading_path=row.get("chunk_heading_path") or "",
            zotero_annotation_key=row.get("zotero_annotation_key") or "",
            server_note_id=row.get("server_note_id") or "",
            client_note_id=row.get("client_note_id") or "",
            bbox=_json_value(row.get("bbox_json")),
            alignment_status=row.get("evidence_alignment_status") or "",
            alignment_confidence=row.get("alignment_confidence") or "",
            warnings=warnings,
            developer_meta={
                "source": "workspace_search",
                "result_layer": "note_results_with_pdf_preview",
                "matched_chunk_ids": _json_list(row.get("matched_chunk_ids_json")),
                "alignment_method": row.get("alignment_method") or "",
            },
        ),
    }


def _source_locator(
    *,
    source_type: str,
    document: dict[str, Any],
    chapter: dict[str, Any],
    page: int | None,
    page_label: str,
    chunk_id: int | None,
    bbox: Any,
    selected_text: str,
    highlight_label: str,
    server_note_id: str = "",
    client_note_id: str = "",
    zotero_annotation_key: str = "",
) -> dict[str, Any]:
    return {
        "source_type": source_type,
        "document_id": document.get("document_id"),
        "chapter_id": chapter.get("id"),
        "pdf_page": page,
        "page_label": page_label,
        "chunk_id": chunk_id,
        "bbox": bbox,
        "selected_text": selected_text,
        "highlight_label": highlight_label,
        "server_note_id": server_note_id,
        "client_note_id": client_note_id,
        "zotero_annotation_key": zotero_annotation_key,
    }


def _locator_contract_definition() -> dict[str, Any]:
    return {
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
        "fallback_policy": "bbox_highlight_then_chunk_locator_then_page_jump_then_text_evidence_warning",
    }


def _source_target(
    *,
    source_kind: str,
    document: dict[str, Any],
    chapter: dict[str, Any],
    page: int | None,
    page_label: str,
    selected_text: str,
    note_text: str,
    chunk_evidence_text: str,
    matched_chunk_id: int | None,
    chunk_heading_path: str,
    zotero_annotation_key: str = "",
    server_note_id: str = "",
    client_note_id: str = "",
    bbox: Any = None,
    alignment_status: str = "",
    alignment_confidence: str = "",
    warnings: list[str] | None = None,
    developer_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "sourceKind": source_kind,
        "documentId": document.get("document_id"),
        "chapterId": chapter.get("id"),
        "documentTitle": document.get("title") or "",
        "page": page,
        "pageLabel": page_label,
        "selectedText": selected_text,
        "noteText": note_text,
        "chunkEvidenceText": chunk_evidence_text,
        "matchedChunkId": matched_chunk_id,
        "chunkHeadingPath": chunk_heading_path,
        "zoteroAnnotationKey": zotero_annotation_key,
        "serverNoteId": server_note_id,
        "clientNoteId": client_note_id,
        "bbox": bbox,
        "alignmentStatus": alignment_status,
        "alignmentConfidence": alignment_confidence,
        "warnings": warnings or [],
        "developerMeta": developer_meta or {},
    }


def _chapter_row(conn: Any, *, document_id: int, chapter_id: int) -> dict[str, Any]:
    if not table_exists(conn, "book_chapters"):
        return {}
    row = conn.execute(
        """
        SELECT id, document_id, chapter_index, title, pdf_page_start, pdf_page_end
        FROM book_chapters
        WHERE document_id = ? AND id = ?
        """,
        (document_id, chapter_id),
    ).fetchone()
    return dict(row) if row else {}


def _object_candidate_count(conn: Any, *, document_id: int, chapter_id: int) -> int:
    if not table_exists(conn, "object_candidates"):
        return 0
    available = set(columns(conn, "object_candidates"))
    if "chapter_id" in available:
        row = conn.execute(
            "SELECT COUNT(*) FROM object_candidates WHERE document_id = ? AND chapter_id = ?",
            (document_id, chapter_id),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT COUNT(*) FROM object_candidates WHERE document_id = ?",
            (document_id,),
        ).fetchone()
    return int(row[0]) if row else 0


def _mechanism_draft_count(conn: Any, *, document_id: int) -> int:
    if not table_exists(conn, "mechanism_draft_candidates"):
        return 0
    row = conn.execute(
        "SELECT COUNT(*) FROM mechanism_draft_candidates WHERE matched_document_id = ?",
        (document_id,),
    ).fetchone()
    return int(row[0]) if row else 0


def _approved_object_candidates(
    conn: Any,
    *,
    document_id: int,
    chapter_id: int,
    limit: int = 24,
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
        "action",
        "final_object_name",
        "final_object_type",
        "source_server_note_ids_json",
        "evidence_chunk_ids_json",
        "page_labels_json",
    }
    if not required.issubset(available):
        return []
    rows = conn.execute(
        """
        SELECT candidate_temp_id, human_review_id, action, final_object_name, final_object_type,
               source_server_note_ids_json, evidence_chunk_ids_json, page_labels_json
        FROM object_candidate_human_review_items
        WHERE document_id = ?
          AND chapter_id = ?
          AND approved_candidate = 1
        ORDER BY id
        LIMIT ?
        """,
        (document_id, chapter_id, max(1, min(int(limit or 24), 50))),
    ).fetchall()
    candidates: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        candidates.append(
            {
                "id": item.get("candidate_temp_id"),
                "candidate_temp_id": item.get("candidate_temp_id"),
                "human_review_id": item.get("human_review_id"),
                "object_name": item.get("final_object_name") or "",
                "object_type": item.get("final_object_type") or "",
                "review_action": item.get("action") or "",
                "source_server_note_ids": _json_list(item.get("source_server_note_ids_json")),
                "evidence_chunk_ids": _json_list(item.get("evidence_chunk_ids_json")),
                "page_labels": _json_list(item.get("page_labels_json")),
                "source_type": "object_candidate",
                "status": "approved_human_review_read_only",
                "object_registry_written": False,
                "relation_generated": False,
                "mechanism_generated": False,
            }
        )
    return candidates


def _query_terms(query: str) -> list[str]:
    normalized = query.strip().lower()
    if not normalized:
        return []
    terms: list[str] = []
    for token in re.findall(r"[a-zA-Z0-9_+-]+", normalized):
        if len(token) >= 2:
            terms.append(token)
    for marker, expansions in QUERY_EXPANSIONS.items():
        if marker in query:
            terms.append(marker)
            terms.extend(expansions)
    if not terms and normalized:
        terms.append(normalized)
    unique: list[str] = []
    for term in terms:
        clean = str(term).strip().lower()
        if clean and clean not in unique:
            unique.append(clean)
    return unique[:MAX_QUERY_TERMS]


def _rank_rows(rows: list[dict[str, Any]], terms: list[str], fields: tuple[str, ...]) -> list[dict[str, Any]]:
    return sorted(
        rows,
        key=lambda row: (-_score_row(row, terms, fields), _page_sort(row), int(row.get("id") or 0)),
    )


def _score_row(row: dict[str, Any], terms: list[str], fields: tuple[str, ...]) -> int:
    if not terms:
        return 0
    text = " ".join(str(row.get(field) or "") for field in fields).lower()
    return sum(3 if term in text else 0 for term in terms)


def _page_sort(row: dict[str, Any]) -> int:
    for key in ("pdf_page_start", "pdf_page", "chunk_page_start"):
        if row.get(key) is not None:
            return int(row.get(key) or 0)
    return 0


def _snippet(text: str, terms: list[str]) -> str:
    normalized = " ".join(str(text or "").split())
    if not normalized:
        return ""
    lower = normalized.lower()
    starts = [lower.find(term) for term in terms if lower.find(term) >= 0]
    start = max(0, min(starts) - 70) if starts else 0
    snippet = normalized[start : start + SNIPPET_CHARS]
    if start > 0:
        snippet = "..." + snippet
    if start + SNIPPET_CHARS < len(normalized):
        snippet += "..."
    return snippet


def _heading_title(heading_path: str) -> str:
    parts = [part.strip() for part in str(heading_path or "").split("/") if part.strip()]
    return parts[-1] if parts else ""


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


def _safety_flags() -> dict[str, bool]:
    return {
        "db_write_performed": False,
        "zotero_write_performed": False,
        "zotero_db_write_performed": False,
        "llm_called": False,
        "object_candidates_generated": False,
        "relation_generated": False,
        "mechanism_generated": False,
        "vector_write_performed": False,
        "vector_store_write_performed": False,
    }
