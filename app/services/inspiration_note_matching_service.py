from __future__ import annotations

import difflib
import json
import re
import sqlite3
import unicodedata
from typing import Any, Mapping


FUZZY_MIN_RATIO = 0.55
FUZZY_MEDIUM_RATIO = 0.78
PUNCTUATION_TRANSLATION = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2013": "-",
        "\u2014": "-",
        "\uff0c": ",",
        "\u3002": ".",
        "\uff1a": ":",
        "\uff1b": ";",
        "\uff01": "!",
        "\uff1f": "?",
    }
)


class InspirationMatchingSchemaUnavailable(RuntimeError):
    pass


def normalize_match_text(value: str | None) -> str:
    text = unicodedata.normalize("NFC", str(value or ""))
    text = text.replace("\u00ad", "").translate(PUNCTUATION_TRANSLATION)
    return re.sub(r"\s+", " ", text).strip().casefold()


def match_inspiration_note_to_document_and_chunk(
    conn: sqlite3.Connection,
    note: Mapping[str, Any],
    *,
    document_id: int | None = None,
    max_candidates: int = 5,
) -> dict[str, Any]:
    _require_chunk_schema(conn)
    max_candidates = max(1, int(max_candidates))
    raw_note = _raw_note_snapshot(note)
    warnings: list[str] = []
    resolved_document_id, resolved_by = _resolve_document_id(conn, note, document_id, warnings)
    selected_text = str(note.get("selected_text") or "")
    selected_normalized = normalize_match_text(selected_text)
    pdf_page = _optional_int(note.get("pdf_page"))

    if not selected_normalized:
        warnings.append("selected_text_empty_matching_skipped")
        return _fallback_report(
            note,
            raw_note,
            resolved_document_id,
            pdf_page,
            warnings,
            "manual_note_without_source_text",
        )

    page_candidates = _page_candidate_rows(conn, resolved_document_id, pdf_page)
    if resolved_document_id is None and pdf_page is not None:
        page_candidates = _page_candidate_rows(conn, None, pdf_page)
        if page_candidates:
            warnings.append("attachment_unmatched_page_search_read_only")

    exact_candidates = _rank_candidates(
        conn,
        page_candidates,
        note,
        exact_only=True,
        warnings=warnings,
    )
    if exact_candidates:
        best = exact_candidates[0]
        inferred_document_id = resolved_document_id or best["document_id"]
        method = (
            "attachment_page_exact_text"
            if resolved_by == "zotero_attachment_key"
            else "page_exact_text"
        )
        if resolved_document_id is None:
            warnings.append("document_inferred_from_page_exact_text")
        return _matched_report(
            note,
            raw_note,
            inferred_document_id,
            pdf_page,
            method,
            "high",
            exact_candidates[:max_candidates],
            warnings,
        )

    if resolved_document_id is not None and pdf_page is not None:
        fuzzy_candidates = _rank_candidates(
            conn,
            page_candidates,
            note,
            exact_only=False,
            warnings=warnings,
        )
        fuzzy_candidates = [
            candidate for candidate in fuzzy_candidates if candidate["score"] >= FUZZY_MIN_RATIO
        ]
        if fuzzy_candidates:
            confidence = (
                "medium"
                if fuzzy_candidates[0]["score"] >= FUZZY_MEDIUM_RATIO
                else "low"
            )
            warnings.append("dry_run_fuzzy_candidate_not_persisted")
            return _matched_report(
                note,
                raw_note,
                resolved_document_id,
                pdf_page,
                "page_fuzzy_text",
                confidence,
                fuzzy_candidates[:max_candidates],
                warnings,
            )

    fallback_reason = (
        "document_resolved_but_no_page_text_match"
        if resolved_document_id is not None
        else "no_document_or_chunk_match"
    )
    return _fallback_report(
        note,
        raw_note,
        resolved_document_id,
        pdf_page,
        warnings,
        fallback_reason,
    )


def match_inspiration_notes_batch(
    conn: sqlite3.Connection,
    notes: list[Mapping[str, Any]],
    *,
    document_id: int | None = None,
    max_candidates: int = 5,
) -> dict[str, Any]:
    reports = [
        match_inspiration_note_to_document_and_chunk(
            conn,
            note,
            document_id=document_id,
            max_candidates=max_candidates,
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
    }


def build_inspiration_match_report(
    conn: sqlite3.Connection,
    note: Mapping[str, Any],
    *,
    document_id: int | None = None,
    max_candidates: int = 5,
) -> dict[str, Any]:
    return match_inspiration_note_to_document_and_chunk(
        conn,
        note,
        document_id=document_id,
        max_candidates=max_candidates,
    )


def _resolve_document_id(
    conn: sqlite3.Connection,
    note: Mapping[str, Any],
    requested_document_id: int | None,
    warnings: list[str],
) -> tuple[int | None, str | None]:
    if requested_document_id is not None:
        if _document_exists(conn, requested_document_id):
            return requested_document_id, "request_document_id"
        warnings.append("requested_document_not_found")
        return None, None

    attachment_key = note.get("zotero_attachment_key")
    if not attachment_key:
        warnings.append("zotero_attachment_key_missing")
        return None, None
    if not _table_exists(conn, "document_sources"):
        warnings.append("document_sources_unavailable")
        return None, None

    rows = conn.execute(
        """
        SELECT DISTINCT document_id
        FROM document_sources
        WHERE zotero_attachment_key = ?
        ORDER BY document_id
        """,
        (attachment_key,),
    ).fetchall()
    if len(rows) == 1:
        return int(rows[0][0]), "zotero_attachment_key"
    if len(rows) > 1:
        warnings.append("ambiguous_attachment_document_mapping")
    else:
        warnings.append("attachment_unmatched")
    return None, None


def _document_exists(conn: sqlite3.Connection, document_id: int) -> bool:
    if _table_exists(conn, "documents"):
        row = conn.execute("SELECT id FROM documents WHERE id = ? LIMIT 1", (document_id,)).fetchone()
        return row is not None
    row = conn.execute(
        "SELECT document_id FROM knowledge_chunks WHERE document_id = ? LIMIT 1",
        (document_id,),
    ).fetchone()
    return row is not None


def _page_candidate_rows(
    conn: sqlite3.Connection,
    document_id: int | None,
    pdf_page: int | None,
) -> list[dict[str, Any]]:
    if pdf_page is None:
        return []
    parameters: list[Any] = [pdf_page, pdf_page]
    where = (
        "pdf_page_start IS NOT NULL AND pdf_page_start <= ? "
        "AND COALESCE(pdf_page_end, pdf_page_start) >= ?"
    )
    if document_id is not None:
        where = "document_id = ? AND " + where
        parameters.insert(0, document_id)
    cursor = conn.execute(
        f"""
        SELECT id AS chunk_id, document_id, chunk_text, pdf_page_start, pdf_page_end
        FROM knowledge_chunks
        WHERE {where}
        ORDER BY document_id, id
        """,
        tuple(parameters),
    )
    columns = [item[0] for item in cursor.description]
    return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def _rank_candidates(
    conn: sqlite3.Connection,
    rows: list[dict[str, Any]],
    note: Mapping[str, Any],
    *,
    exact_only: bool,
    warnings: list[str],
) -> list[dict[str, Any]]:
    selected = normalize_match_text(str(note.get("selected_text") or ""))
    context_before = normalize_match_text(_optional_text(note.get("context_before")))
    context_after = normalize_match_text(_optional_text(note.get("context_after")))
    ranked: list[tuple[float, dict[str, Any]]] = []
    bbox_warned = False

    for row in rows:
        chunk_text = normalize_match_text(str(row.get("chunk_text") or ""))
        exact_score, exact_reason = _exact_score(selected, chunk_text)
        if exact_only and exact_score == 0:
            continue
        if not exact_only:
            exact_score = _fuzzy_score(selected, chunk_text)
            exact_reason = "normalized selected_text similarity on same page"
        context_bonus, context_reason = _context_bonus(context_before, context_after, chunk_text)
        bbox_bonus, bbox_reason, bbox_available = _bbox_bonus(conn, note.get("bbox"), row)
        if note.get("bbox") is not None and not bbox_available and not bbox_warned:
            warnings.append("bbox_present_no_readable_layout_anchor")
            bbox_warned = True
        ranking_score = exact_score + context_bonus + bbox_bonus
        reason_parts = [exact_reason]
        if context_reason:
            reason_parts.append(context_reason)
        if bbox_reason:
            reason_parts.append(bbox_reason)
        public_candidate = {
            "chunk_id": int(row["chunk_id"]),
            "document_id": int(row["document_id"]),
            "pdf_page_start": row["pdf_page_start"],
            "pdf_page_end": row["pdf_page_end"],
            "score": round(min(1.0, exact_score + bbox_bonus), 4),
            "reason": "; ".join(reason_parts),
            "_chunk_text": row["chunk_text"],
        }
        ranked.append((ranking_score, public_candidate))
    ranked.sort(key=lambda value: (-value[0], value[1]["chunk_id"]))
    return [candidate for _, candidate in ranked]


def _exact_score(selected: str, chunk_text: str) -> tuple[float, str]:
    if not selected or not chunk_text:
        return 0.0, ""
    if selected in chunk_text:
        return 1.0, "selected_text exact substring on same page"
    if len(chunk_text) >= 32 and chunk_text in selected:
        return 0.96, "long selected_text contains the complete same-page chunk window"
    return 0.0, ""


def _fuzzy_score(selected: str, chunk_text: str) -> float:
    if not selected or not chunk_text:
        return 0.0
    ratios = [difflib.SequenceMatcher(None, selected, chunk_text).ratio()]
    if len(chunk_text) > len(selected):
        window = max(len(selected), 1)
        step = max(window // 4, 1)
        for start in range(0, max(len(chunk_text) - window + 1, 1), step):
            ratios.append(
                difflib.SequenceMatcher(None, selected, chunk_text[start : start + window]).ratio()
            )
        ratios.append(difflib.SequenceMatcher(None, selected, chunk_text[-window:]).ratio())
    return max(ratios)


def _context_bonus(before: str, after: str, chunk_text: str) -> tuple[float, str | None]:
    matched = []
    if before and before in chunk_text:
        matched.append("context_before")
    if after and after in chunk_text:
        matched.append("context_after")
    if not matched:
        return 0.0, None
    return 0.03 * len(matched), "matched " + " and ".join(matched)


def _bbox_bonus(
    conn: sqlite3.Connection,
    bbox: Any,
    row: Mapping[str, Any],
) -> tuple[float, str | None, bool]:
    note_rects = _rectangles(bbox)
    if not note_rects:
        return 0.0, None, bbox is None
    layout_rects = _layout_rectangles_for_chunk(conn, int(row["chunk_id"]), int(row["document_id"]))
    if not layout_rects:
        return 0.0, None, False
    maximum = max((_intersection_fraction(left, right) for left in note_rects for right in layout_rects), default=0.0)
    if maximum <= 0:
        return 0.0, None, True
    return min(0.04, maximum * 0.04), "bbox overlaps existing read-only layout anchor", True


def _layout_rectangles_for_chunk(
    conn: sqlite3.Connection,
    chunk_id: int,
    document_id: int,
) -> list[tuple[float, float, float, float]]:
    if _table_exists(conn, "chunk_layout_line_links") and _table_exists(conn, "pdf_page_layout_lines"):
        rows = conn.execute(
            """
            SELECT lines.bbox_json
            FROM chunk_layout_line_links AS links
            JOIN pdf_page_layout_lines AS lines ON lines.id = links.line_id
            WHERE links.chunk_id = ? AND links.document_id = ?
            """,
            (chunk_id, document_id),
        ).fetchall()
        return [rect for row in rows for rect in _rectangles(_json_or_none(row[0]))]
    if _table_exists(conn, "chunk_layout_links") and _table_exists(conn, "pdf_page_layout_blocks"):
        rows = conn.execute(
            """
            SELECT blocks.bbox_json
            FROM chunk_layout_links AS links
            JOIN pdf_page_layout_blocks AS blocks ON blocks.id = links.block_id
            WHERE links.chunk_id = ? AND links.document_id = ?
            """,
            (chunk_id, document_id),
        ).fetchall()
        return [rect for row in rows for rect in _rectangles(_json_or_none(row[0]))]
    return []


def _rectangles(value: Any) -> list[tuple[float, float, float, float]]:
    if not value:
        return []
    if isinstance(value, Mapping) and "rects" in value:
        values = value["rects"]
    elif isinstance(value, Mapping):
        values = [value]
    elif isinstance(value, list):
        values = value
    else:
        return []
    rectangles = []
    for item in values:
        try:
            if isinstance(item, Mapping):
                rect = (
                    float(item["x0"]),
                    float(item["y0"]),
                    float(item["x1"]),
                    float(item["y1"]),
                )
            else:
                rect = tuple(float(number) for number in item[:4])
            if len(rect) == 4:
                rectangles.append(rect)
        except (KeyError, TypeError, ValueError):
            continue
    return rectangles


def _intersection_fraction(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
) -> float:
    x0 = max(left[0], right[0])
    y0 = max(left[1], right[1])
    x1 = min(left[2], right[2])
    y1 = min(left[3], right[3])
    overlap = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    left_area = max(0.0, left[2] - left[0]) * max(0.0, left[3] - left[1])
    return overlap / left_area if left_area else 0.0


def _matched_report(
    note: Mapping[str, Any],
    raw_note: dict[str, Any],
    document_id: int,
    pdf_page: int | None,
    method: str,
    confidence: str,
    candidates: list[dict[str, Any]],
    warnings: list[str],
) -> dict[str, Any]:
    best = candidates[0]
    public_candidates = [{key: value for key, value in item.items() if not key.startswith("_")} for item in candidates]
    return {
        "status": "OK",
        "client_note_id": str(note.get("client_note_id") or ""),
        "server_note_id": note.get("server_note_id"),
        "matched_document_id": document_id,
        "matched_chunk_id": best["chunk_id"],
        "matched_pdf_page": pdf_page,
        "match_method": method,
        "match_confidence": confidence,
        "selected_text_preserved": True,
        "note_text_preserved": True,
        "user_tags_preserved": True,
        "candidate_chunks": public_candidates,
        "evidence_context": {
            "chunk_text": best["_chunk_text"],
            "context_before": note.get("context_before"),
            "context_after": note.get("context_after"),
        },
        "raw_note": raw_note,
        "warnings": list(dict.fromkeys(warnings)),
        "db_write_performed": False,
        "mechanism_generated": False,
        "llm_called": False,
    }


def _fallback_report(
    note: Mapping[str, Any],
    raw_note: dict[str, Any],
    document_id: int | None,
    pdf_page: int | None,
    warnings: list[str],
    reason: str,
) -> dict[str, Any]:
    matched_document_only = document_id is not None
    return {
        "status": "OK",
        "client_note_id": str(note.get("client_note_id") or ""),
        "server_note_id": note.get("server_note_id"),
        "matched_document_id": document_id,
        "matched_chunk_id": None,
        "matched_pdf_page": pdf_page if matched_document_only else None,
        "match_method": "attachment_only" if matched_document_only else "unmatched",
        "match_confidence": "low" if matched_document_only else "none",
        "selected_text_preserved": True,
        "note_text_preserved": True,
        "user_tags_preserved": True,
        "candidate_chunks": [],
        "evidence_context": None,
        "raw_note": raw_note,
        "warnings": list(dict.fromkeys([*warnings, reason])),
        "db_write_performed": False,
        "mechanism_generated": False,
        "llm_called": False,
    }


def _raw_note_snapshot(note: Mapping[str, Any]) -> dict[str, Any]:
    tags = note.get("user_tags")
    if tags is None and note.get("user_tags_json") is not None:
        tags = _json_or_none(str(note["user_tags_json"]))
    return {
        "selected_text": note.get("selected_text"),
        "note_text": note.get("note_text"),
        "user_tags": list(tags or []),
    }


def _require_chunk_schema(conn: sqlite3.Connection) -> None:
    if not _table_exists(conn, "knowledge_chunks"):
        raise InspirationMatchingSchemaUnavailable("knowledge_chunks is unavailable for dry-run matching.")
    columns = _columns(conn, "knowledge_chunks")
    required = {"id", "document_id", "chunk_text", "pdf_page_start", "pdf_page_end"}
    missing = required - columns
    if missing:
        raise InspirationMatchingSchemaUnavailable(
            f"knowledge_chunks lacks required dry-run columns: {', '.join(sorted(missing))}."
        )


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _json_or_none(value: str) -> Any:
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def _optional_text(value: Any) -> str | None:
    return None if value is None else str(value)
