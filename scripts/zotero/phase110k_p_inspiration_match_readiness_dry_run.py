from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import unicodedata
from pathlib import Path
from typing import Any, Mapping

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.paths import DEFAULT_DB_PATH


MODE = "phase110k_p_inspiration_match_readiness_dry_run_v1"
ALIGNMENT_MODE = "zotero_note_import_time_evidence_alignment_dry_run"
APPROVED_OBJECT_STATES = {"accepted", "approved", "edited"}
PENDING_OBJECT_STATES = {"candidate", "pending", "pending_review", "suggested", "unreviewed"}
REJECTED_OBJECT_STATES = {"deprecated", "rejected"}
HIGH_DOCUMENT_CONFIDENCE = 0.85
HIGH_CHUNK_SCORE = 0.82
MEDIUM_CHUNK_SCORE = 0.55
CHUNK_PAGE_WINDOW = 2
SPAN_SCORE_IMPROVEMENT = 0.1


def build_readiness_dry_run_report(
    db_path: str | Path = DEFAULT_DB_PATH,
    *,
    client_note_id: str | None = None,
    server_note_id: str | None = None,
    attachment_key: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    db = Path(db_path)
    with sqlite3.connect(_sqlite_uri(db), uri=True) as connection:
        connection.row_factory = sqlite3.Row
        notes = _load_notes(
            connection,
            client_note_id=client_note_id,
            server_note_id=server_note_id,
            attachment_key=attachment_key,
            limit=limit,
        )
        items = [_readiness_for_note(connection, note) for note in notes]
    return {
        "status": "OK",
        "mode": MODE,
        "alignment_mode": ALIGNMENT_MODE,
        "description": (
            "Read-only Zotero note import-time evidence alignment dry-run; "
            "this is not user search and does not write matched fields."
        ),
        "db_path": str(db),
        "count": len(items),
        "items": items,
        "db_write_performed": False,
        "mechanism_generated": False,
        "llm_called": False,
        "vector_store_write_performed": False,
        "mechanism_draft_candidates_write_performed": False,
    }


def _load_notes(
    conn: sqlite3.Connection,
    *,
    client_note_id: str | None,
    server_note_id: str | None,
    attachment_key: str | None,
    limit: int | None,
) -> list[sqlite3.Row]:
    if not _table_exists(conn, "zotero_inspiration_notes"):
        return []
    where = ["1 = 1"]
    params: list[Any] = []
    if client_note_id:
        where.append("client_note_id = ?")
        params.append(client_note_id)
    if server_note_id:
        where.append("server_note_id = ?")
        params.append(server_note_id)
    if attachment_key:
        where.append("zotero_attachment_key = ?")
        params.append(attachment_key)
    limit_sql = ""
    if limit is not None:
        limit_sql = " LIMIT ?"
        params.append(max(0, int(limit)))
    return conn.execute(
        f"""
        SELECT *
        FROM zotero_inspiration_notes
        WHERE {' AND '.join(where)}
        ORDER BY id
        {limit_sql}
        """,
        tuple(params),
    ).fetchall()


def _readiness_for_note(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    note = dict(row)
    note["user_tags"] = _json_list(note.get("user_tags_json"))
    warnings: list[str] = []
    blockers: list[str] = []

    document_match = _match_documents(conn, note)
    warnings.extend(document_match["warnings"])
    chunk_match = _match_chunks(conn, note, document_match["candidates"])
    warnings.extend(chunk_match["warnings"])
    document_match = _corroborate_document_match(note, document_match, chunk_match)
    object_match = _match_objects(conn, chunk_match["candidates"])
    warnings.extend(object_match["warnings"])

    document_status = _document_match_status(document_match["candidates"])
    chunk_status = _chunk_match_status(
        chunk_match["candidates"],
        chunk_match["span_candidates"],
    )
    approved_objects = [
        item for item in object_match["object_candidates"]
        if item["approval_status"] == "approved"
    ]
    object_status = _object_readiness_status(object_match, approved_objects)
    alignment = _evidence_alignment_summary(chunk_match)
    mechanism_status = "ready_for_prompt"

    if not str(note.get("note_text") or "").strip():
        blockers.append("note_text_empty")
    if not str(note.get("selected_text") or "").strip():
        blockers.append("selected_text_empty")
    if _note_text_may_be_test_or_low_quality(note):
        warnings.append("note_text_may_be_test_or_low_quality")
        blockers.append("note_text_low_quality")
    if document_status != "matched":
        blockers.append(f"document_{document_status}")
    if chunk_status != "matched":
        blockers.append(f"chunk_{chunk_status}")
    if not approved_objects:
        blockers.append("approved_object_missing")
    if blockers:
        mechanism_status = "blocked"

    return {
        "note_id": note.get("id"),
        "server_note_id": note.get("server_note_id"),
        "client_note_id": note.get("client_note_id"),
        "zotero_attachment_key": note.get("zotero_attachment_key"),
        "zotero_item_key": note.get("zotero_item_key"),
        "pdf_page": note.get("pdf_page"),
        "page_label": note.get("page_label"),
        "note_text": note.get("note_text"),
        "selected_text": note.get("selected_text"),
        "alignment_mode": ALIGNMENT_MODE,
        "evidence_alignment_status": alignment["evidence_alignment_status"],
        "alignment_confidence": alignment["alignment_confidence"],
        "alignment_method": alignment["alignment_method"],
        "alignment_warnings": list(dict.fromkeys(warnings)),
        "document_match_status": document_status,
        "chunk_match_status": chunk_status,
        "object_readiness_status": object_status,
        "mechanism_readiness_status": mechanism_status,
        "document_candidates": document_match["candidates"],
        "chunk_candidates": chunk_match["candidates"],
        "chunk_span_candidates": chunk_match["span_candidates"],
        "object_candidates": object_match["object_candidates"],
        "blockers": list(dict.fromkeys(blockers)),
        "warnings": list(dict.fromkeys(warnings)),
        "recommended_next_action": _recommended_next_action(
            document_status,
            chunk_status,
            object_status,
            mechanism_status,
        ),
        "db_write_performed": False,
        "mechanism_generated": False,
        "llm_called": False,
    }


def _match_documents(conn: sqlite3.Connection, note: Mapping[str, Any]) -> dict[str, Any]:
    candidates: dict[int, dict[str, Any]] = {}
    warnings: list[str] = []
    attachment_key = _text_or_none(note.get("zotero_attachment_key"))
    item_key = _text_or_none(note.get("zotero_item_key"))

    if not _table_exists(conn, "documents"):
        return {"candidates": [], "warnings": ["documents_table_unavailable"]}

    if attachment_key and _table_has_columns(
        conn, "document_sources", {"document_id", "zotero_attachment_key"}
    ):
        rows = _query_dicts(
            conn,
            """
            SELECT document_id
            FROM document_sources
            WHERE zotero_attachment_key = ?
            ORDER BY document_id
            """,
            (attachment_key,),
        )
        for row in rows:
            _add_document_candidate(
                conn,
                candidates,
                int(row["document_id"]),
                0.95,
                "document_sources.zotero_attachment_key exact match",
            )

    if item_key and _table_has_columns(conn, "document_sources", {"document_id", "zotero_item_key"}):
        rows = _query_dicts(
            conn,
            """
            SELECT document_id
            FROM document_sources
            WHERE zotero_item_key = ?
            ORDER BY document_id
            """,
            (item_key,),
        )
        for row in rows:
            _add_document_candidate(
                conn,
                candidates,
                int(row["document_id"]),
                0.88,
                "document_sources.zotero_item_key exact match",
            )

    if item_key and _table_has_columns(conn, "documents", {"id", "zotero_key"}):
        rows = _query_dicts(
            conn,
            "SELECT id AS document_id FROM documents WHERE zotero_key = ? ORDER BY id",
            (item_key,),
        )
        for row in rows:
            _add_document_candidate(
                conn,
                candidates,
                int(row["document_id"]),
                0.82,
                "documents.zotero_key exact match",
            )

    if attachment_key:
        _match_document_by_pdf_source(conn, attachment_key, candidates)

    if not candidates:
        warnings.append("attachment_document_unmatched")
    ordered = sorted(candidates.values(), key=lambda item: (-item["confidence"], item["document_id"]))
    return {"candidates": ordered[:5], "warnings": warnings}


def _match_document_by_pdf_source(
    conn: sqlite3.Connection,
    attachment_key: str,
    candidates: dict[int, dict[str, Any]],
) -> None:
    if not _table_has_columns(conn, "zotero_pdf_sources", {"zotero_attachment_key"}):
        return
    source_columns = _columns(conn, "zotero_pdf_sources")
    selected = [
        "zotero_attachment_key",
        _select_expr(source_columns, "resolved_pdf_path"),
        _select_expr(source_columns, "attachment_path_raw"),
        _select_expr(source_columns, "title"),
    ]
    source = _query_one_dict(
        conn,
        f"""
        SELECT {', '.join(selected)}
        FROM zotero_pdf_sources
        WHERE zotero_attachment_key = ?
        LIMIT 1
        """,
        (attachment_key,),
    )
    if not source:
        return
    doc_columns = _columns(conn, "documents")
    doc_selected = ["id AS document_id"]
    for column in ("title", "source_path", "pdf_path"):
        doc_selected.append(_select_expr(doc_columns, column))
    documents = _query_dicts(conn, f"SELECT {', '.join(doc_selected)} FROM documents")
    source_paths = {
        _normalize_path(value)
        for value in (source.get("resolved_pdf_path"), source.get("attachment_path_raw"))
        if value
    }
    source_title = _normalize_match_text(source.get("title"))
    for document in documents:
        document_paths = {
            _normalize_path(value)
            for value in (document.get("source_path"), document.get("pdf_path"))
            if value
        }
        if source_paths and source_paths.intersection(document_paths):
            _add_document_candidate(
                conn,
                candidates,
                int(document["document_id"]),
                0.78,
                "zotero_pdf_sources path equals document source path",
            )
        elif source_title and source_title == _normalize_match_text(document.get("title")):
            _add_document_candidate(
                conn,
                candidates,
                int(document["document_id"]),
                0.6,
                "zotero_pdf_sources title exact match; weak dry-run signal",
            )


def _add_document_candidate(
    conn: sqlite3.Connection,
    candidates: dict[int, dict[str, Any]],
    document_id: int,
    confidence: float,
    evidence: str,
) -> None:
    summary = _document_summary(conn, document_id)
    current = candidates.get(document_id)
    if current is None:
        candidates[document_id] = {
            "document_id": document_id,
            "document_title": summary.get("document_title"),
            "pdf_path": summary.get("pdf_path"),
            "source_path": summary.get("source_path"),
            "confidence": round(confidence, 3),
            "evidence": [evidence],
            "failure_reason": None,
        }
        return
    current["confidence"] = round(max(float(current["confidence"]), confidence), 3)
    current["evidence"].append(evidence)


def _document_summary(conn: sqlite3.Connection, document_id: int) -> dict[str, Any]:
    columns = _columns(conn, "documents")
    selected = ["id AS document_id"]
    for column in ("title", "pdf_path", "source_path"):
        selected.append(_select_expr(columns, column))
    row = _query_one_dict(
        conn,
        f"SELECT {', '.join(selected)} FROM documents WHERE id = ?",
        (document_id,),
    )
    if not row:
        return {"document_title": None, "pdf_path": None, "source_path": None}
    return {
        "document_title": row.get("title"),
        "pdf_path": row.get("pdf_path"),
        "source_path": row.get("source_path"),
    }


def _match_chunks(
    conn: sqlite3.Connection,
    note: Mapping[str, Any],
    document_candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    warnings: list[str] = []
    selected_text = str(note.get("selected_text") or "")
    selected_norm = _normalize_match_text(selected_text)
    if not selected_norm:
        return {"candidates": [], "span_candidates": [], "warnings": ["selected_text_empty"]}
    if not _table_has_columns(conn, "knowledge_chunks", {"id", "document_id", "chunk_text"}):
        return {"candidates": [], "span_candidates": [], "warnings": ["knowledge_chunks_unavailable"]}

    high_document_ids = [
        int(item["document_id"])
        for item in document_candidates
        if float(item["confidence"]) >= HIGH_DOCUMENT_CONFIDENCE
    ]
    document_ids = high_document_ids or [
        int(item["document_id"]) for item in document_candidates
    ]
    if not document_ids:
        return {
            "candidates": [],
            "span_candidates": [],
            "warnings": ["document_unmatched_chunk_search_skipped"],
        }

    rows = _candidate_chunk_rows(conn, document_ids, _optional_int(note.get("pdf_page")))
    ranked: list[dict[str, Any]] = []
    for row in rows:
        score, reason = _chunk_text_score(selected_norm, str(row.get("chunk_text") or ""))
        if score <= 0:
            continue
        distance = _page_distance(_optional_int(note.get("pdf_page")), row)
        if distance is not None and distance > 0:
            score = max(0.0, score - min(0.12, distance * 0.05))
            reason = f"{reason}; nearby_page_fallback:+{distance}"
            warnings.append("nearby_page_fallback_used")
        confidence = _chunk_confidence(score)
        ranked.append(
            {
                "chunk_id": int(row["chunk_id"]),
                "document_id": int(row["document_id"]),
                "chunk_text_snippet": _snippet(str(row.get("chunk_text") or ""), 220),
                "heading_path": row.get("heading_path"),
                "pdf_page_start": row.get("pdf_page_start"),
                "pdf_page_end": row.get("pdf_page_end"),
                "score": round(score, 4),
                "confidence": confidence,
                "reason": reason,
            }
        )
    ranked.sort(key=lambda item: (-float(item["score"]), item["chunk_id"]))
    span_candidates = _match_chunk_spans(
        note,
        rows,
        selected_norm,
        ranked,
    )
    return {
        "candidates": ranked[:5],
        "span_candidates": span_candidates[:5],
        "warnings": warnings,
    }


def _match_chunk_spans(
    note: Mapping[str, Any],
    rows: list[dict[str, Any]],
    selected_norm: str,
    single_candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    best_single_score = max(
        [float(item["score"]) for item in single_candidates],
        default=0.0,
    )
    pdf_page = _optional_int(note.get("pdf_page"))
    spans: list[dict[str, Any]] = []
    ordered = sorted(
        rows,
        key=lambda item: (
            int(item["document_id"]),
            _optional_int(item.get("pdf_page_start")) or -1,
            int(item["chunk_id"]),
        ),
    )
    for left, right in zip(ordered, ordered[1:]):
        if not _chunks_are_adjacent_span(left, right):
            continue
        score, _ = _chunk_text_score(
            selected_norm,
            f"{left.get('chunk_text') or ''} {right.get('chunk_text') or ''}",
        )
        if score < MEDIUM_CHUNK_SCORE:
            continue
        if score < best_single_score + SPAN_SCORE_IMPROVEMENT:
            continue
        page_distance = _span_page_distance(pdf_page, [left, right])
        if page_distance is not None and page_distance > 1:
            continue
        heading_path = left.get("heading_path") or right.get("heading_path")
        spans.append(
            {
                "chunk_ids": [int(left["chunk_id"]), int(right["chunk_id"])],
                "document_id": int(left["document_id"]),
                "heading_path": heading_path,
                "pdf_page_start": _span_page_start([left, right]),
                "pdf_page_end": _span_page_end([left, right]),
                "score": round(score, 4),
                "confidence": _chunk_confidence(score),
                "reason": "selected_text spans adjacent chunks",
                "snippet": _snippet(
                    f"{left.get('chunk_text') or ''} {right.get('chunk_text') or ''}",
                    260,
                ),
            }
        )
    spans.sort(key=lambda item: (-float(item["score"]), item["chunk_ids"][0]))
    return spans


def _chunks_are_adjacent_span(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
) -> bool:
    if int(left["document_id"]) != int(right["document_id"]):
        return False
    if _page_gap(left, right) > 1:
        return False
    left_id = int(left["chunk_id"])
    right_id = int(right["chunk_id"])
    if right_id == left_id + 1:
        return True
    left_heading = str(left.get("heading_path") or "").strip()
    right_heading = str(right.get("heading_path") or "").strip()
    return bool(left_heading and left_heading == right_heading)


def _page_gap(left: Mapping[str, Any], right: Mapping[str, Any]) -> int:
    left_start = _optional_int(left.get("pdf_page_start"))
    left_end = _optional_int(left.get("pdf_page_end")) or left_start
    right_start = _optional_int(right.get("pdf_page_start"))
    right_end = _optional_int(right.get("pdf_page_end")) or right_start
    if left_start is None or left_end is None or right_start is None or right_end is None:
        return 0
    if right_start <= left_end:
        return 0
    if left_start <= right_end and right_start <= left_end:
        return 0
    return min(abs(right_start - left_end), abs(left_start - right_end))


def _span_page_distance(
    pdf_page: int | None,
    rows: list[Mapping[str, Any]],
) -> int | None:
    if pdf_page is None:
        return None
    starts = [
        value for value in (_optional_int(row.get("pdf_page_start")) for row in rows)
        if value is not None
    ]
    ends = [
        value for value in (_optional_int(row.get("pdf_page_end")) for row in rows)
        if value is not None
    ]
    if not starts or not ends:
        return None
    start = min(starts)
    end = max(ends)
    if start <= pdf_page <= end:
        return 0
    return min(abs(pdf_page - start), abs(pdf_page - end))


def _span_page_start(rows: list[Mapping[str, Any]]) -> int | None:
    starts = [
        value for value in (_optional_int(row.get("pdf_page_start")) for row in rows)
        if value is not None
    ]
    return min(starts) if starts else None


def _span_page_end(rows: list[Mapping[str, Any]]) -> int | None:
    ends = [
        value for value in (_optional_int(row.get("pdf_page_end")) for row in rows)
        if value is not None
    ]
    return max(ends) if ends else None


def _candidate_chunk_rows(
    conn: sqlite3.Connection,
    document_ids: list[int],
    pdf_page: int | None,
) -> list[dict[str, Any]]:
    columns = _columns(conn, "knowledge_chunks")
    selected = [
        "id AS chunk_id",
        "document_id",
        "chunk_text",
        _select_expr(columns, "heading_path"),
        _select_expr(columns, "pdf_page_start"),
        _select_expr(columns, "pdf_page_end"),
    ]
    placeholders = ", ".join(["?"] * len(document_ids))
    params: list[Any] = list(document_ids)
    where = [f"document_id IN ({placeholders})"]
    if pdf_page is not None and "pdf_page_start" in columns:
        where.append(
            """
            pdf_page_start IS NOT NULL
            AND pdf_page_start BETWEEN ? AND ?
            """
        )
        params.extend([pdf_page - CHUNK_PAGE_WINDOW, pdf_page + CHUNK_PAGE_WINDOW])
    return _query_dicts(
        conn,
        f"""
        SELECT {', '.join(selected)}
        FROM knowledge_chunks
        WHERE {' AND '.join(where)}
        ORDER BY document_id, id
        LIMIT 200
        """,
        tuple(params),
    )


def _chunk_text_score(selected_norm: str, chunk_text: str) -> tuple[float, str]:
    chunk_norm = _normalize_match_text(chunk_text)
    if selected_norm and selected_norm in chunk_norm:
        return 1.0, "selected_text exact substring"
    selected_tokens = _tokens(selected_norm)
    chunk_tokens = _tokens(chunk_norm)
    if not selected_tokens or not chunk_tokens:
        return 0.0, "selected_text_or_chunk_empty"
    overlap = len(selected_tokens.intersection(chunk_tokens)) / max(1, len(selected_tokens))
    if overlap >= 0.25:
        return overlap, "selected_text token overlap"
    return 0.0, "weak_text_overlap_below_threshold"


def _chunk_confidence(score: float) -> str:
    if score >= HIGH_CHUNK_SCORE:
        return "high"
    if score >= MEDIUM_CHUNK_SCORE:
        return "medium"
    return "low"


def _page_distance(pdf_page: int | None, row: Mapping[str, Any]) -> int | None:
    chunk_page = _optional_int(row.get("pdf_page_start"))
    if pdf_page is None or chunk_page is None:
        return None
    return abs(chunk_page - pdf_page)


def _match_objects(
    conn: sqlite3.Connection,
    chunk_candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    if not chunk_candidates:
        return {
            "object_candidates": [],
            "warnings": ["chunk_unmatched_object_search_skipped"],
        }
    if not _table_has_columns(
        conn,
        "object_candidates",
        {"id", "object_name", "object_type", "review_status"},
    ):
        return {
            "object_candidates": [],
            "warnings": ["object_candidates_unavailable"],
        }
    chunk_ids = [int(item["chunk_id"]) for item in chunk_candidates]
    document_ids = {int(item["document_id"]) for item in chunk_candidates}
    selected_columns = _object_select_columns(conn)
    rows = _query_dicts(conn, f"SELECT {', '.join(selected_columns)} FROM object_candidates")
    rows_with_chunk_ids = [(row, _object_chunk_ids(row)) for row in rows]
    mapped_ids = [
        chunk_id
        for _, mapped_chunk_ids in rows_with_chunk_ids
        for chunk_id in mapped_chunk_ids
    ]
    chunk_page_by_id = _chunk_page_map(conn, chunk_ids + mapped_ids)
    objects: list[dict[str, Any]] = []
    for row, mapped_chunk_ids in rows_with_chunk_ids:
        if row.get("document_id") is not None and int(row["document_id"]) not in document_ids:
            continue
        reason = _object_link_reason(mapped_chunk_ids, chunk_ids, chunk_page_by_id)
        if reason is None:
            continue
        review_status = str(row.get("review_status") or "unknown")
        approval_status = _approval_status(review_status)
        objects.append(
            {
                "object_id": row.get("id"),
                "object_key": row.get("object_key"),
                "object_name": row.get("object_name"),
                "object_type": row.get("object_type"),
                "review_status": review_status,
                "approval_status": approval_status,
                "mapped_chunk_ids": mapped_chunk_ids,
                "evidence_refs": _json_list(row.get("evidence_refs_json")),
                "confidence": "high" if reason == "mapped_chunk_id_match" else "medium",
                "reason": reason,
            }
        )
    objects.sort(
        key=lambda item: (
            0 if item["approval_status"] == "approved" else 1,
            str(item.get("object_name") or ""),
        )
    )
    return {"object_candidates": objects, "warnings": []}


def _object_select_columns(conn: sqlite3.Connection) -> list[str]:
    columns = _columns(conn, "object_candidates")
    selected = ["id", "object_name", "object_type", "review_status"]
    for column in (
        "object_key",
        "document_id",
        "mapped_chunk_ids_json",
        "evidence_refs_json",
    ):
        selected.append(_select_expr(columns, column))
    return selected


def _object_chunk_ids(row: Mapping[str, Any]) -> list[int]:
    values = [_optional_int(value) for value in _json_list(row.get("mapped_chunk_ids_json"))]
    chunk_ids = [value for value in values if value is not None]
    refs = _json_list(row.get("evidence_refs_json"))
    for ref in refs:
        if isinstance(ref, Mapping):
            value = _optional_int(ref.get("chunk_id"))
            if value is not None:
                chunk_ids.append(value)
    return list(dict.fromkeys(chunk_ids))


def _object_link_reason(
    mapped_chunk_ids: list[int],
    matched_chunk_ids: list[int],
    chunk_page_by_id: Mapping[int, int | None],
) -> str | None:
    if set(mapped_chunk_ids).intersection(matched_chunk_ids):
        return "mapped_chunk_id_match"
    matched_pages = {
        page for chunk_id, page in chunk_page_by_id.items()
        if chunk_id in matched_chunk_ids and page is not None
    }
    mapped_pages = {
        page for chunk_id, page in chunk_page_by_id.items()
        if chunk_id in mapped_chunk_ids and page is not None
    }
    if matched_pages and mapped_pages:
        for left in matched_pages:
            for right in mapped_pages:
                if abs(left - right) <= 1:
                    return "nearby_mapped_chunk_page"
    return None


def _chunk_page_map(conn: sqlite3.Connection, chunk_ids: list[int]) -> dict[int, int | None]:
    all_ids = list(dict.fromkeys(chunk_ids))
    if not all_ids or not _table_has_columns(conn, "knowledge_chunks", {"id"}):
        return {}
    placeholders = ", ".join(["?"] * len(all_ids))
    columns = _columns(conn, "knowledge_chunks")
    rows = _query_dicts(
        conn,
        f"""
        SELECT id AS chunk_id, {_select_expr(columns, "pdf_page_start")}
        FROM knowledge_chunks
        WHERE id IN ({placeholders})
        """,
        tuple(all_ids),
    )
    return {int(row["chunk_id"]): _optional_int(row.get("pdf_page_start")) for row in rows}


def _corroborate_document_match(
    note: Mapping[str, Any],
    document_match: Mapping[str, Any],
    chunk_match: Mapping[str, Any],
) -> dict[str, Any]:
    candidates = [dict(item) for item in document_match["candidates"]]
    if not candidates:
        return {"candidates": candidates, "warnings": document_match["warnings"]}
    if _document_match_status(candidates) == "matched":
        return {"candidates": candidates, "warnings": document_match["warnings"]}
    candidate = _unique_document_candidate_for_corroboration(candidates)
    if candidate is None:
        return {"candidates": candidates, "warnings": document_match["warnings"]}
    reason = _document_corroboration_reason(note, candidate, chunk_match)
    if reason is None:
        return {"candidates": candidates, "warnings": document_match["warnings"]}
    candidate["corroborated_by_chunk"] = True
    candidate["corroboration_reason"] = reason
    candidate["evidence"] = list(candidate.get("evidence") or []) + [reason]
    return {"candidates": candidates, "warnings": document_match["warnings"]}


def _unique_document_candidate_for_corroboration(
    candidates: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if len(candidates) == 1:
        return candidates[0]
    ordered = sorted(candidates, key=lambda item: -float(item["confidence"]))
    top = ordered[0]
    runner_up = ordered[1]
    if (
        float(top["confidence"]) >= HIGH_DOCUMENT_CONFIDENCE
        and float(top["confidence"]) - float(runner_up["confidence"]) >= 0.2
    ):
        return top
    return None


def _document_corroboration_reason(
    note: Mapping[str, Any],
    candidate: Mapping[str, Any],
    chunk_match: Mapping[str, Any],
) -> str | None:
    document_id = int(candidate["document_id"])
    for span in chunk_match.get("span_candidates") or []:
        if int(span["document_id"]) != document_id:
            continue
        if span["confidence"] == "high" and _candidate_same_or_near_page(note, span):
            return "unique title candidate + same-page text overlap"
    for chunk in chunk_match.get("candidates") or []:
        if int(chunk["document_id"]) != document_id:
            continue
        if float(chunk["score"]) >= MEDIUM_CHUNK_SCORE and _candidate_same_or_near_page(note, chunk):
            return "document candidate corroborated by same-page chunk evidence"
    return None


def _candidate_same_or_near_page(
    note: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> bool:
    pdf_page = _optional_int(note.get("pdf_page"))
    if pdf_page is None:
        return True
    start = _optional_int(candidate.get("pdf_page_start"))
    end = _optional_int(candidate.get("pdf_page_end")) or start
    if start is None or end is None:
        return False
    if start <= pdf_page <= end:
        return True
    return min(abs(pdf_page - start), abs(pdf_page - end)) <= 1


def _document_match_status(candidates: list[dict[str, Any]]) -> str:
    corroborated = [item for item in candidates if item.get("corroborated_by_chunk") is True]
    if len(corroborated) == 1:
        return "matched"
    high = [item for item in candidates if float(item["confidence"]) >= HIGH_DOCUMENT_CONFIDENCE]
    if len(high) == 1:
        return "matched"
    if candidates:
        return "ambiguous"
    return "unmatched"


def _chunk_match_status(
    candidates: list[dict[str, Any]],
    span_candidates: list[dict[str, Any]] | None = None,
) -> str:
    high_spans = [
        item for item in (span_candidates or [])
        if item["confidence"] == "high"
    ]
    if high_spans:
        return "matched"
    high = [item for item in candidates if item["confidence"] == "high"]
    if high:
        return "matched"
    if candidates:
        return "ambiguous"
    return "unmatched"


def _evidence_alignment_summary(chunk_match: Mapping[str, Any]) -> dict[str, Any]:
    span_candidates = list(chunk_match.get("span_candidates") or [])
    high_spans = [
        item for item in span_candidates
        if item["confidence"] == "high"
    ]
    if high_spans:
        return {
            "evidence_alignment_status": "span_matched",
            "alignment_confidence": high_spans[0]["confidence"],
            "alignment_method": "adjacent_chunk_span_text_overlap",
        }
    candidates = list(chunk_match.get("candidates") or [])
    high = [item for item in candidates if item["confidence"] == "high"]
    if high:
        return {
            "evidence_alignment_status": "matched",
            "alignment_confidence": high[0]["confidence"],
            "alignment_method": "single_chunk_text_overlap",
        }
    if candidates:
        return {
            "evidence_alignment_status": "ambiguous",
            "alignment_confidence": candidates[0]["confidence"],
            "alignment_method": "candidate_text_overlap",
        }
    return {
        "evidence_alignment_status": "unmatched",
        "alignment_confidence": "none",
        "alignment_method": "no_candidate",
    }


def _object_readiness_status(
    object_match: Mapping[str, Any],
    approved_objects: list[dict[str, Any]],
) -> str:
    if approved_objects:
        return "ready"
    if object_match.get("object_candidates"):
        return "partial"
    return "blocked"


def _recommended_next_action(
    document_status: str,
    chunk_status: str,
    object_status: str,
    mechanism_status: str,
) -> str:
    if document_status == "unmatched":
        return "link_zotero_attachment_to_document_before_matching"
    if document_status == "ambiguous":
        return "review_document_candidates_before_matching"
    if chunk_status == "unmatched":
        return "review_page_and_selected_text_chunk_match"
    if chunk_status == "ambiguous":
        return "review_top_chunk_candidates_before_prompt"
    if object_status != "ready":
        return "review_or_map_approved_object_candidates_before_prompt"
    if mechanism_status == "ready_for_prompt":
        return "ready_for_manual_prompt_preview"
    return "review_blockers_before_prompt"


def _note_text_may_be_test_or_low_quality(note: Mapping[str, Any]) -> bool:
    tags = {str(item) for item in note.get("user_tags") or []}
    if tags.intersection({"__kl_real_capture_test__", "__kp_real_match_test__"}):
        return True
    note_text = str(note.get("note_text") or "").strip()
    if not note_text:
        return False
    alnum_count = sum(1 for char in note_text if char.isalnum())
    return len(note_text) < 8 or alnum_count < 4


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only K-P-A Zotero note import-time evidence alignment "
            "and mechanism readiness dry-run."
        )
    )
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--client-note-id")
    parser.add_argument("--server-note-id")
    parser.add_argument("--attachment-key")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--json", action="store_true", help="Print JSON output.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = build_readiness_dry_run_report(
        args.db_path,
        client_note_id=args.client_note_id,
        server_note_id=args.server_note_id,
        attachment_key=args.attachment_key,
        limit=args.limit,
    )
    if args.json:
        _print_json(report)
    else:
        print(report)
    return 0


def _print_json(report: Mapping[str, Any]) -> None:
    output = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    buffer = getattr(sys.stdout, "buffer", None)
    if buffer is None:
        print(output, end="")
        return
    buffer.write(output.encode("utf-8"))


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    if not _table_exists(conn, table):
        return set()
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _table_has_columns(conn: sqlite3.Connection, table: str, required: set[str]) -> bool:
    return required.issubset(_columns(conn, table))


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


def _select_expr(columns: set[str], column: str) -> str:
    return column if column in columns else f"NULL AS {column}"


def _json_list(value: Any) -> list[Any]:
    try:
        parsed = json.loads(str(value or "[]"))
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def _normalize_match_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    return re.sub(r"\s+", " ", text).strip().casefold()


def _normalize_path(value: Any) -> str:
    return str(value or "").replace("\\", "/").strip().casefold()


def _tokens(value: str) -> set[str]:
    return {token for token in re.findall(r"[\w]+", value.casefold()) if len(token) > 1}


def _snippet(value: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", value).strip()
    return text if len(text) <= limit else text[:limit] + "..."


def _approval_status(review_status: str) -> str:
    status = str(review_status or "").strip().casefold()
    if status in APPROVED_OBJECT_STATES:
        return "approved"
    if status in PENDING_OBJECT_STATES:
        return "candidate"
    if status in REJECTED_OBJECT_STATES:
        return "rejected"
    return "unreviewed"


def _optional_int(value: Any) -> int | None:
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


def _text_or_none(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _sqlite_uri(db_path: Path) -> str:
    return f"file:{db_path.resolve().as_posix()}?mode=ro"


if __name__ == "__main__":
    raise SystemExit(main())
