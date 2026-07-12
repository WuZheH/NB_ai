from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

from app.core.paths import DEFAULT_DB_PATH
from app.domains.database_search.filters import (
    _item_contains_any_term,
    _item_matches_anchor_groups,
    _items_contain_any_term,
    _items_match_anchor_groups,
    _match_reason,
    _rank_result_items,
    _rank_rows,
    _score_row,
    _term_first_index,
    _term_occurrences,
)
from app.domains.database_search.mapping import (
    _first_int,
    _json_ints,
    _json_list,
    _json_value,
    _note_identity,
    _numeric_score,
    _safety_flags,
    _stable_chunk_id,
    _stable_note_id,
)
from app.domains.database_search.pagination import _dedupe_results, _page_sort
from app.domains.database_search.query import (
    ALL_LAYER_KEYS,
    _compact_text,
    _escape_like,
    _like_predicate,
    _requested_layers,
    _scope_expansion_state,
    _scope_predicates,
)
from app.services.citation_renderer_service import add_citations_to_results
from app.services.search_query_expansion_service import expand_query
from app.services.unit_note_object_processing_service import columns, connect_readonly, table_exists


DEFAULT_LIMIT = 10
MAX_LIMIT = 25
MAX_CANDIDATE_ROWS = 300
SNIPPET_CHARS = 340
MAX_RELATED_KEYWORDS = 8

RELATED_KEYWORD_STOPWORDS = {
    "about",
    "across",
    "algorithm",
    "and",
    "analysis",
    "approach",
    "based",
    "between",
    "chapter",
    "data",
    "datasets",
    "document",
    "effect",
    "evidence",
    "example",
    "experiment",
    "figure",
    "finding",
    "for",
    "from",
    "introduction",
    "learning",
    "machine",
    "method",
    "model",
    "models",
    "note",
    "paper",
    "performance",
    "result",
    "results",
    "section",
    "study",
    "table",
    "task",
    "the",
    "using",
    "with",
}

def build_database_search(
    *,
    research_db_path: str | Path = DEFAULT_DB_PATH,
    query: str,
    document_id: int | None = None,
    chapter_id: int | None = None,
    limit: int = DEFAULT_LIMIT,
    include_layers: str | None = None,
) -> dict[str, Any]:
    clean_query = _compact_text(query)
    if not clean_query:
        raise ValueError("q must not be empty.")

    db_path = Path(research_db_path)
    safe_limit = max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))
    requested_layers = _requested_layers(include_layers)
    expansion = expand_query(clean_query)
    terms = list(expansion["terms"])
    scope_anchor_terms = list(expansion.get("scope_anchor_terms") or [])
    scope_anchor_groups = list(expansion.get("scope_anchor_groups") or [])
    filters = {"document_id": document_id, "chapter_id": chapter_id}
    warnings: list[str] = list(expansion.get("warnings") or [])
    scope_expansion = _scope_expansion_state(
        document_id=document_id,
        chapter_id=chapter_id,
        anchor_terms=scope_anchor_terms,
    )
    scope_expansion["anchor_groups"] = scope_anchor_groups

    with connect_readonly(db_path) as conn:
        conn.execute("PRAGMA query_only = ON")
        chapter = _chapter_scope(conn, document_id=document_id, chapter_id=chapter_id)
        chapter_evidence = _run_layer(
            warnings,
            "evidence_chunks",
            requested_layers,
            lambda: _search_evidence_chunks(
                conn,
                terms=terms,
                document_id=document_id,
                chapter_id=chapter_id,
                limit=safe_limit,
            ),
        )
        chapter_evidence = _with_context_reason(chapter_evidence, "direct_match")
        chapter_context = _nearby_chunk_items(
            conn,
            chapter_evidence,
            terms=terms,
            context_window=int(expansion["context_window"]),
            limit=safe_limit,
        )

        fallback_evidence: list[dict[str, Any]] = []
        fallback_context: list[dict[str, Any]] = []
        if (
            "evidence_chunks" in requested_layers
            and document_id is not None
            and chapter_id is not None
            and scope_anchor_terms
            and not _items_match_anchor_groups(chapter_evidence, scope_anchor_groups)
        ):
            document_candidates = _search_evidence_chunks(
                conn,
                terms=terms,
                document_id=document_id,
                chapter_id=None,
                limit=safe_limit,
            )
            fallback_evidence = [
                item
                for item in document_candidates
                if _item_matches_anchor_groups(item, scope_anchor_groups)
            ]
            if fallback_evidence:
                fallback_evidence = _with_scope_reason(
                    _with_context_reason(fallback_evidence, "document_scope_fallback"),
                    "chapter_anchor_missing_document_match",
                )
                fallback_context = _with_scope_reason(
                    _nearby_chunk_items(
                        conn,
                        fallback_evidence,
                        terms=terms,
                        context_window=int(expansion["context_window"]),
                        limit=safe_limit,
                    ),
                    "chapter_anchor_missing_document_match",
                )
                scope_expansion.update(
                    {
                        "applied": True,
                        "effective_evidence_scope": "document",
                        "reason": "no_topic_anchor_in_requested_chapter",
                    }
                )
                warnings.append(
                    "evidence scope expanded from chapter to document: no topic anchor matched in the requested chapter"
                )
            else:
                scope_expansion["reason"] = "no_topic_anchor_in_requested_chapter_or_document"
        elif scope_anchor_terms and chapter_id is not None:
            scope_expansion["reason"] = "topic_anchor_matched_requested_chapter"

        evidence_chunks = _dedupe_results(
            [
                *fallback_evidence,
                *fallback_context,
                *chapter_evidence,
                *chapter_context,
            ],
            "id",
        )
        evidence_chunks = _rank_result_items(evidence_chunks)[: safe_limit * 2]
        zotero_notes = _run_layer(
            warnings,
            "zotero_notes",
            requested_layers,
            lambda: _search_zotero_notes(
                conn,
                terms=terms,
                document_id=document_id,
                chapter=chapter,
                limit=safe_limit,
            ),
        )
        zotero_notes = _dedupe_results(
            [
                *_with_context_reason(zotero_notes, "direct_match"),
                *_note_neighbor_items(
                    conn,
                    evidence_chunks,
                    terms=terms,
                    document_id=document_id,
                    limit=safe_limit,
                ),
            ],
            "id",
        )
        objects = _run_layer(
            warnings,
            "objects",
            requested_layers,
            lambda: _search_objects(
                conn,
                terms=terms,
                document_id=document_id,
                chapter_id=chapter_id,
                limit=safe_limit,
            ),
        )
        objects = _dedupe_results(
            [
                *_with_context_reason(objects, "direct_match"),
                *_object_neighbor_items(
                    conn,
                    evidence_chunks,
                    terms=terms,
                    document_id=document_id,
                    chapter_id=None if scope_expansion["applied"] else chapter_id,
                    limit=safe_limit,
                ),
            ],
            "id",
        )
        mechanisms = _run_layer(
            warnings,
            "mechanisms",
            requested_layers,
            lambda: _search_mechanisms(
                conn,
                terms=terms,
                document_id=document_id,
                limit=safe_limit,
            ),
        )
        mechanisms = _with_context_reason(mechanisms, "direct_match")
        notes_in_scope = _notes_in_scope_count(conn, document_id=document_id, chapter=chapter)
        table_status = {
            "knowledge_chunks": table_exists(conn, "knowledge_chunks"),
            "zotero_inspiration_notes": table_exists(conn, "zotero_inspiration_notes"),
            "object_candidates": table_exists(conn, "object_candidates"),
            "mechanism_draft_candidates": table_exists(conn, "mechanism_draft_candidates"),
        }

    if table_status["mechanism_draft_candidates"] and not mechanisms:
        warnings.append("mechanism layer read-only: no existing mechanism draft matched; no mechanism generated")
    if "zotero_notes" in requested_layers and document_id and chapter_id and notes_in_scope == 0:
        warnings.append("zotero_notes layer unavailable in selected scope: no notes in scope")

    results = {
        "evidence_chunks": evidence_chunks,
        "zotero_notes": zotero_notes,
        "objects": objects,
        "mechanisms": mechanisms,
    }
    results = add_citations_to_results(results)
    counts = {key: len(value) for key, value in results.items()}
    safety = _safety_flags()
    structured = _structured_result(clean_query, results, safety, expansion, scope_expansion)
    research_evidence_packet = _research_evidence_packet_payload(clean_query, results)
    structured["retrieval_results"] = research_evidence_packet["retrieval_results"]
    structured["quality_summary"] = research_evidence_packet["quality_summary"]
    structured["evidence_packet"] = research_evidence_packet["evidence_packet"]
    structured["evidence_packet_markdown"] = research_evidence_packet["evidence_packet_markdown"]
    structured["evidence_packet_json"] = research_evidence_packet["evidence_packet_json"]
    structured["related_keywords"] = research_evidence_packet["related_keywords"]
    structured["research_evidence_packet"] = research_evidence_packet
    layers = _workspace_layers(
        results=results,
        table_status=table_status,
        notes_in_scope=notes_in_scope,
        warnings=warnings,
    )
    return {
        "status": "ok",
        "mode": "database_research_search_read_only_v1",
        "query": clean_query,
        "filters": filters,
        "terms": terms,
        "expanded_terms": expansion["expanded_terms"],
        "expansion_rules": expansion["expansion_rules"],
        "context_window": expansion["context_window"],
        "neighbor_expansion": expansion["neighbor_expansion"],
        "scope_expansion": scope_expansion,
        "results": results,
        "counts": counts,
        "warnings": warnings,
        "structured_retrieval_result": structured,
        "retrieval_results": research_evidence_packet["retrieval_results"],
        "quality_summary": research_evidence_packet["quality_summary"],
        "evidence_packet": research_evidence_packet["evidence_packet"],
        "evidence_packet_markdown": research_evidence_packet["evidence_packet_markdown"],
        "evidence_packet_json": research_evidence_packet["evidence_packet_json"],
        "related_keywords": research_evidence_packet["related_keywords"],
        "research_evidence_packet": research_evidence_packet,
        "layers": layers,
        "workspace_search_role": "database_research_search_structured_retrieval",
        "implementation_status": "connected",
        "safety_flags": safety,
        **safety,
    }


def _run_layer(
    warnings: list[str],
    layer: str,
    requested_layers: set[str],
    callback: Callable[[], list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    if layer not in requested_layers:
        return []
    try:
        return callback()
    except Exception as exc:
        warnings.append(f"{layer} layer degraded: {type(exc).__name__}: {exc}")
        return []


def _search_evidence_chunks(
    conn: Any,
    *,
    terms: list[str],
    document_id: int | None,
    chapter_id: int | None,
    limit: int,
) -> list[dict[str, Any]]:
    if not table_exists(conn, "knowledge_chunks"):
        return []
    predicates, params = _scope_predicates("k", document_id=document_id, chapter_id=chapter_id)
    match_sql, match_params = _like_predicate(
        ("k.chunk_text", "k.heading_path", "d.title", "bc.title"),
        terms,
    )
    predicates.append(match_sql)
    params.extend(match_params)
    rows = conn.execute(
        f"""
        SELECT k.id, k.document_id, k.chapter_id, k.heading_path, k.chunk_text,
               k.pdf_page_start, k.pdf_page_end,
               d.title AS document_title, d.document_type,
               bc.title AS chapter_title
        FROM knowledge_chunks k
        JOIN documents d ON d.id = k.document_id
        LEFT JOIN book_chapters bc ON bc.id = k.chapter_id
        WHERE {' AND '.join(predicates)}
        ORDER BY k.document_id, k.chapter_id, k.pdf_page_start, k.id
        LIMIT ?
        """,
        (*params, MAX_CANDIDATE_ROWS),
    ).fetchall()
    ranked = _rank_rows([dict(row) for row in rows], terms, ("chunk_text", "heading_path", "document_title", "chapter_title"))
    return [_evidence_item(row, terms=terms) for row in ranked[:limit]]


def _search_zotero_notes(
    conn: Any,
    *,
    terms: list[str],
    document_id: int | None,
    chapter: dict[str, Any],
    limit: int,
) -> list[dict[str, Any]]:
    if not table_exists(conn, "zotero_inspiration_notes"):
        return []
    predicates: list[str] = []
    params: list[Any] = []
    if document_id is not None:
        predicates.append("n.matched_document_id = ?")
        params.append(document_id)
    if chapter:
        start = chapter.get("pdf_page_start")
        end = chapter.get("pdf_page_end") or start
        if start is not None and end is not None:
            predicates.append("n.pdf_page BETWEEN ? AND ?")
            params.extend([start, end])
    match_sql, match_params = _like_predicate(
        (
            "n.note_text",
            "n.selected_text",
            "n.user_tags_json",
            "n.server_note_id",
            "n.client_note_id",
            "n.zotero_annotation_key",
            "k.chunk_text",
        ),
        terms,
    )
    predicates.append(match_sql)
    params.extend(match_params)
    where_sql = " AND ".join(predicates) if predicates else match_sql
    rows = conn.execute(
        f"""
        SELECT n.id, n.server_note_id, n.client_note_id, n.source,
               n.zotero_annotation_key, n.pdf_page, n.page_label,
               n.selected_text, n.note_text, n.user_tags_json, n.bbox_json,
               n.matched_document_id, n.matched_chunk_id, n.matched_chunk_ids_json,
               n.review_status, n.mechanism_status, n.evidence_alignment_status,
               n.alignment_confidence, n.alignment_method,
               k.heading_path AS chunk_heading_path,
               k.chunk_text AS chunk_evidence_text,
               d.title AS document_title
        FROM zotero_inspiration_notes n
        LEFT JOIN knowledge_chunks k ON k.id = n.matched_chunk_id
        LEFT JOIN documents d ON d.id = n.matched_document_id
        WHERE {where_sql}
        ORDER BY n.matched_document_id, n.pdf_page, n.id
        LIMIT ?
        """,
        (*params, MAX_CANDIDATE_ROWS),
    ).fetchall()
    ranked = _rank_rows(
        [dict(row) for row in rows],
        terms,
        ("note_text", "selected_text", "user_tags_json", "chunk_evidence_text", "server_note_id", "client_note_id"),
    )
    return [_zotero_note_item(row, terms=terms) for row in ranked[:limit]]


def _search_objects(
    conn: Any,
    *,
    terms: list[str],
    document_id: int | None,
    chapter_id: int | None,
    limit: int,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    if table_exists(conn, "object_candidates"):
        predicates, params = _scope_predicates("o", document_id=document_id, chapter_id=chapter_id)
        if chapter_id is not None and "chapter_id" in columns(conn, "object_candidates"):
            predicates[-1] = "(o.chapter_id = ? OR o.chapter_id IS NULL)"
        match_sql, match_params = _like_predicate(
            (
                "o.object_name",
                "o.object_key",
                "o.object_type",
                "o.review_status",
                "o.status",
                "o.confidence",
                "o.aliases_json",
                "o.description",
                "o.topic_tags_json",
                "o.problem_tags_json",
                "o.mechanism_tags_json",
                "o.inspiration_tags_json",
            ),
            terms,
        )
        predicates.append(match_sql)
        params.extend(match_params)
        rows = conn.execute(
            f"""
            SELECT o.id, o.document_id, o.chapter_id, o.object_key, o.object_name,
                   o.object_type, o.review_status, o.status, o.confidence,
                   o.aliases_json, o.description, o.mapped_chunk_ids_json,
                   o.evidence_refs_json, o.mechanism_tags_json,
                   d.title AS document_title
            FROM object_candidates o
            LEFT JOIN documents d ON d.id = o.document_id
            WHERE {' AND '.join(predicates)}
            ORDER BY o.document_id, o.id
            LIMIT ?
            """,
            (*params, MAX_CANDIDATE_ROWS),
        ).fetchall()
        ranked = _rank_rows(
            [dict(row) for row in rows],
            terms,
            ("object_name", "object_key", "object_type", "aliases_json", "description", "mechanism_tags_json"),
        )
        results.extend(_object_candidate_item(conn, row, terms=terms) for row in ranked)

    if table_exists(conn, "object_candidate_human_review_items"):
        predicates: list[str] = ["h.approved_candidate = 1"]
        params: list[Any] = []
        if document_id is not None:
            predicates.append("h.document_id = ?")
            params.append(document_id)
        if chapter_id is not None:
            predicates.append("h.chapter_id = ?")
            params.append(chapter_id)
        match_sql, match_params = _like_predicate(
            (
                "h.final_object_name",
                "h.final_object_type",
                "h.action",
                "h.candidate_temp_id",
                "h.source_labels_json",
                "h.human_note",
            ),
            terms,
        )
        predicates.append(match_sql)
        params.extend(match_params)
        rows = conn.execute(
            f"""
            SELECT h.candidate_temp_id, h.human_review_id, h.document_id, h.chapter_id,
                   h.action, h.final_object_name, h.final_object_type,
                   h.source_server_note_ids_json, h.evidence_chunk_ids_json,
                   h.page_labels_json, h.human_note,
                   d.title AS document_title
            FROM object_candidate_human_review_items h
            LEFT JOIN documents d ON d.id = h.document_id
            WHERE {' AND '.join(predicates)}
            ORDER BY h.id
            LIMIT ?
            """,
            (*params, MAX_CANDIDATE_ROWS),
        ).fetchall()
        ranked = _rank_rows(
            [dict(row) for row in rows],
            terms,
            ("final_object_name", "final_object_type", "candidate_temp_id", "human_note"),
        )
        results.extend(_human_review_object_item(conn, row, terms=terms) for row in ranked)

    return _dedupe_results(results, "id")[:limit]


def _search_mechanisms(
    conn: Any,
    *,
    terms: list[str],
    document_id: int | None,
    limit: int,
) -> list[dict[str, Any]]:
    if not table_exists(conn, "mechanism_draft_candidates"):
        return []
    predicates: list[str] = []
    params: list[Any] = []
    if document_id is not None:
        predicates.append("m.matched_document_id = ?")
        params.append(document_id)
    match_sql, match_params = _like_predicate(
        (
            "m.draft_id",
            "m.mechanism_key",
            "m.mechanism_name_cn",
            "m.mechanism_name_en",
            "m.mechanism_type",
            "m.confidence",
            "m.review_status",
            "m.review_decision",
            "m.review_notes",
            "m.draft_json",
        ),
        terms,
    )
    predicates.append(match_sql)
    params.extend(match_params)
    rows = conn.execute(
        f"""
        SELECT m.id, m.draft_id, m.source, m.matched_document_id,
               m.evidence_chunk_ids_json, m.pdf_pages_json,
               m.mechanism_key, m.mechanism_name_cn, m.mechanism_name_en,
               m.mechanism_type, m.confidence, m.review_status, m.review_decision,
               m.review_notes, m.draft_json,
               d.title AS document_title
        FROM mechanism_draft_candidates m
        LEFT JOIN documents d ON d.id = m.matched_document_id
        WHERE {' AND '.join(predicates)}
        ORDER BY m.id
        LIMIT ?
        """,
        (*params, MAX_CANDIDATE_ROWS),
    ).fetchall()
    ranked = _rank_rows(
        [dict(row) for row in rows],
        terms,
        ("mechanism_name_cn", "mechanism_name_en", "mechanism_type", "review_notes", "draft_json"),
    )
    return [_mechanism_item(conn, row, terms=terms) for row in ranked[:limit]]


def _evidence_item(row: dict[str, Any], *, terms: list[str]) -> dict[str, Any]:
    chunk_id = row.get("id")
    page = row.get("pdf_page_start")
    document_id = row.get("document_id")
    text = str(row.get("chunk_text") or "")
    stable_id = _stable_chunk_id(document_id, page, chunk_id)
    return {
        "id": f"chunk-{chunk_id}",
        "stable_id": stable_id,
        "source_kind": "passage",
        "source_type": "chunk",
        "retrieval_source_type": "pdf_chunk",
        "source_entity_id": chunk_id,
        "chunk_id": chunk_id,
        "document_id": document_id,
        "document_title": row.get("document_title") or "",
        "chapter_id": row.get("chapter_id"),
        "chapter_title": row.get("chapter_title") or "",
        "title": _heading_title(row.get("heading_path") or "") or row.get("document_title") or f"chunk {chunk_id}",
        "heading_path": row.get("heading_path") or "",
        "page": page,
        "pdf_page": page,
        "page_label": f"p.{page}" if page else "",
        "snippet": _snippet(text, terms),
        "text": text,
        "score": _score_row(row, terms, ("chunk_text", "heading_path", "document_title", "chapter_title")),
        "raw_text_ref": f"chunk:{chunk_id}",
        "citation_token": f"doc:{document_id}|chunk:{chunk_id}|page:{page}" if chunk_id is not None else "",
        "match_reason": _match_reason(row, terms, ("chunk_text", "heading_path", "document_title", "chapter_title")),
        "source_trace": {
            "source_kind": "passage",
            "source_type": "chunk",
            "document_id": document_id,
            "chapter_id": row.get("chapter_id"),
            "chunk_id": chunk_id,
            "pdf_page": page,
        },
        "source_target": _source_target(
            source_kind="passage",
            row=row,
            page=page,
            chunk_id=chunk_id,
            chunk_evidence_text=text,
        ),
    }


def _zotero_note_item(row: dict[str, Any], *, terms: list[str]) -> dict[str, Any]:
    page = row.get("pdf_page")
    note_text = str(row.get("note_text") or "")
    selected_text = str(row.get("selected_text") or "")
    chunk_text = str(row.get("chunk_evidence_text") or "")
    note_identity = _note_identity(row)
    stable_id = _stable_note_id(note_identity)
    return {
        "id": f"note-{row.get('id')}",
        "stable_id": stable_id,
        "source_kind": "note",
        "source_type": "note",
        "retrieval_source_type": "zotero_note",
        "source_entity_id": note_identity,
        "server_note_id": row.get("server_note_id"),
        "client_note_id": row.get("client_note_id"),
        "note_id": row.get("server_note_id") or row.get("client_note_id"),
        "zotero_annotation_key": row.get("zotero_annotation_key"),
        "document_id": row.get("matched_document_id"),
        "document_title": row.get("document_title") or "",
        "page": page,
        "pdf_page": page,
        "page_label": row.get("page_label") or (f"p.{page}" if page else ""),
        "chunk_id": row.get("matched_chunk_id"),
        "matched_chunk_ids": _json_list(row.get("matched_chunk_ids_json")),
        "matched_object_ids": _json_list(row.get("matched_object_ids_json")),
        "title": note_text[:80] or row.get("server_note_id") or "Zotero note",
        "snippet": _snippet(" ".join(part for part in [note_text, selected_text, chunk_text] if part), terms),
        "note_text": note_text,
        "selected_text": selected_text,
        "tags": _json_list(row.get("user_tags_json")),
        "user_tags": _json_list(row.get("user_tags_json")),
        "chunk_evidence_text": chunk_text,
        "review_status": row.get("review_status"),
        "mechanism_status": row.get("mechanism_status"),
        "review_badge": "review not saved",
        "score": _score_row(row, terms, ("note_text", "selected_text", "user_tags_json", "chunk_evidence_text")),
        "raw_text_ref": f"note:{note_identity}" if note_identity else "",
        "citation_token": f"note:{note_identity}" if note_identity else "",
        "match_reason": _match_reason(row, terms, ("note_text", "selected_text", "user_tags_json", "chunk_evidence_text")),
        "source_trace": {
            "source_kind": "note",
            "source_type": "note",
            "document_id": row.get("matched_document_id"),
            "chunk_id": row.get("matched_chunk_id"),
            "pdf_page": page,
            "server_note_id": row.get("server_note_id"),
            "client_note_id": row.get("client_note_id"),
        },
        "source_target": _source_target(
            source_kind="note",
            row={
                **row,
                "document_id": row.get("matched_document_id"),
                "heading_path": row.get("chunk_heading_path"),
            },
            page=page,
            chunk_id=row.get("matched_chunk_id"),
            selected_text=selected_text,
            note_text=note_text,
            chunk_evidence_text=chunk_text,
            zotero_annotation_key=row.get("zotero_annotation_key") or "",
            server_note_id=row.get("server_note_id") or "",
            client_note_id=row.get("client_note_id") or "",
            bbox=_json_value(row.get("bbox_json")),
        ),
    }


def _object_candidate_item(conn: Any, row: dict[str, Any], *, terms: list[str]) -> dict[str, Any]:
    chunk_ids = _json_list(row.get("mapped_chunk_ids_json"))
    chunk = _chunk_by_id(conn, _first_int(chunk_ids))
    return {
        "id": f"object-{row.get('id')}",
        "source_kind": "object_candidate",
        "source_type": "object_candidate",
        "object_candidate_id": row.get("id"),
        "label": row.get("object_name") or row.get("object_key") or "",
        "title": row.get("object_name") or row.get("object_key") or "Object candidate",
        "object_name": row.get("object_name") or "",
        "object_key": row.get("object_key") or "",
        "type": row.get("object_type") or "",
        "object_type": row.get("object_type") or "",
        "review_status": row.get("review_status") or "",
        "status": row.get("status") or "",
        "confidence": row.get("confidence") or "",
        "document_id": row.get("document_id"),
        "document_title": row.get("document_title") or "",
        "chapter_id": row.get("chapter_id"),
        "source_chunk_ids": chunk_ids,
        "snippet": _snippet(
            " ".join(
                str(row.get(key) or "")
                for key in ("object_name", "object_type", "description", "aliases_json", "mechanism_tags_json")
            ),
            terms,
        ),
        "score": _score_row(row, terms, ("object_name", "object_key", "object_type", "aliases_json", "description", "mechanism_tags_json")),
        "match_reason": _match_reason(row, terms, ("object_name", "object_key", "object_type", "aliases_json", "description", "mechanism_tags_json")),
        "source_target": _source_target_from_chunk("object_evidence", row, chunk),
    }


def _human_review_object_item(conn: Any, row: dict[str, Any], *, terms: list[str]) -> dict[str, Any]:
    chunk_ids = _json_list(row.get("evidence_chunk_ids_json"))
    chunk = _chunk_by_id(conn, _first_int(chunk_ids))
    source_note_ids = _json_list(row.get("source_server_note_ids_json"))
    return {
        "id": f"approved-object-{row.get('candidate_temp_id')}",
        "source_kind": "object_candidate",
        "source_type": "object_candidate",
        "object_candidate_id": row.get("candidate_temp_id"),
        "candidate_temp_id": row.get("candidate_temp_id"),
        "label": row.get("final_object_name") or "",
        "title": row.get("final_object_name") or row.get("candidate_temp_id") or "Approved object candidate",
        "type": row.get("final_object_type") or "",
        "object_type": row.get("final_object_type") or "",
        "review_status": "approved_human_review_read_only",
        "status": row.get("action") or "",
        "document_id": row.get("document_id"),
        "document_title": row.get("document_title") or "",
        "chapter_id": row.get("chapter_id"),
        "source_server_note_ids": source_note_ids,
        "source_chunk_ids": chunk_ids,
        "page_labels": _json_list(row.get("page_labels_json")),
        "snippet": _snippet(" ".join(str(row.get(key) or "") for key in ("final_object_name", "final_object_type", "human_note")), terms),
        "score": _score_row(row, terms, ("final_object_name", "final_object_type", "human_note", "candidate_temp_id")),
        "match_reason": _match_reason(row, terms, ("final_object_name", "final_object_type", "human_note", "candidate_temp_id")),
        "source_target": _source_target_from_chunk(
            "object_evidence",
            {**row, "object_candidate_id": row.get("candidate_temp_id")},
            chunk,
            server_note_id=str(source_note_ids[0]) if source_note_ids else "",
        ),
    }


def _mechanism_item(conn: Any, row: dict[str, Any], *, terms: list[str]) -> dict[str, Any]:
    chunk_ids = _json_list(row.get("evidence_chunk_ids_json"))
    chunk = _chunk_by_id(conn, _first_int(chunk_ids))
    name = row.get("mechanism_name_cn") or row.get("mechanism_name_en") or row.get("mechanism_key")
    return {
        "id": f"mechanism-{row.get('draft_id') or row.get('id')}",
        "source_kind": "mechanism_evidence",
        "source_type": "mechanism_evidence",
        "draft_id": row.get("draft_id"),
        "title": name or "Mechanism draft",
        "label": name or "",
        "mechanism_key": row.get("mechanism_key") or "",
        "mechanism_type": row.get("mechanism_type") or "",
        "review_status": row.get("review_status") or "",
        "review_decision": row.get("review_decision") or "",
        "confidence": row.get("confidence") or "",
        "document_id": row.get("matched_document_id"),
        "document_title": row.get("document_title") or "",
        "source_chunk_ids": chunk_ids,
        "snippet": _snippet(" ".join(str(row.get(key) or "") for key in ("mechanism_name_cn", "mechanism_name_en", "mechanism_type", "review_notes", "draft_json")), terms),
        "score": _score_row(row, terms, ("mechanism_name_cn", "mechanism_name_en", "mechanism_type", "review_notes", "draft_json")),
        "match_reason": _match_reason(row, terms, ("mechanism_name_cn", "mechanism_name_en", "mechanism_type", "review_notes", "draft_json")),
        "mechanism_generated": False,
        "source_target": _source_target_from_chunk("mechanism_evidence", row, chunk),
    }


def _with_context_reason(items: list[dict[str, Any]], reason: str) -> list[dict[str, Any]]:
    results = []
    for item in items:
        enriched = dict(item)
        enriched.setdefault("context_reason", reason)
        results.append(enriched)
    return results


def _with_scope_reason(items: list[dict[str, Any]], reason: str) -> list[dict[str, Any]]:
    results = []
    for item in items:
        enriched = dict(item)
        enriched["scope_reason"] = reason
        results.append(enriched)
    return results


def _nearby_chunk_items(
    conn: Any,
    direct_items: list[dict[str, Any]],
    *,
    terms: list[str],
    context_window: int,
    limit: int,
) -> list[dict[str, Any]]:
    if not direct_items or not table_exists(conn, "knowledge_chunks"):
        return []
    results: list[dict[str, Any]] = []
    seen = {item.get("chunk_id") for item in direct_items}
    for item in direct_items[:limit]:
        chunk_id = _first_int([item.get("chunk_id")])
        document_id = _first_int([item.get("document_id")])
        chapter_id = _first_int([item.get("chapter_id")])
        if chunk_id is None or document_id is None:
            continue
        predicates = ["k.document_id = ?", "k.id != ?", "ABS(k.id - ?) <= ?"]
        params: list[Any] = [document_id, chunk_id, chunk_id, max(1, int(context_window))]
        if chapter_id is not None:
            predicates.append("k.chapter_id = ?")
            params.append(chapter_id)
        rows = conn.execute(
            f"""
            SELECT k.id, k.document_id, k.chapter_id, k.heading_path, k.chunk_text,
                   k.pdf_page_start, k.pdf_page_end,
                   d.title AS document_title, d.document_type,
                   bc.title AS chapter_title
            FROM knowledge_chunks k
            JOIN documents d ON d.id = k.document_id
            LEFT JOIN book_chapters bc ON bc.id = k.chapter_id
            WHERE {' AND '.join(predicates)}
            ORDER BY ABS(k.id - ?), k.id
            LIMIT ?
            """,
            (*params, chunk_id, max(1, limit)),
        ).fetchall()
        for row in rows:
            row_dict = dict(row)
            if row_dict.get("id") in seen:
                continue
            seen.add(row_dict.get("id"))
            result = _evidence_item(row_dict, terms=terms)
            result["match_reason"] = "nearby_chunk"
            result["context_reason"] = "nearby_chunk"
            result["direct_match_chunk_id"] = chunk_id
            results.append(result)
    return results[:limit]


def _note_neighbor_items(
    conn: Any,
    evidence_items: list[dict[str, Any]],
    *,
    terms: list[str],
    document_id: int | None,
    limit: int,
) -> list[dict[str, Any]]:
    if not evidence_items or not table_exists(conn, "zotero_inspiration_notes"):
        return []
    chunk_ids = _chunk_ids(evidence_items)
    if not chunk_ids:
        return []
    placeholders = ", ".join("?" for _ in chunk_ids)
    predicates = [f"n.matched_chunk_id IN ({placeholders})"]
    params: list[Any] = list(chunk_ids)
    if document_id is not None:
        predicates.append("n.matched_document_id = ?")
        params.append(document_id)
    rows = conn.execute(
        f"""
        SELECT n.id, n.server_note_id, n.client_note_id, n.source,
               n.zotero_annotation_key, n.pdf_page, n.page_label,
               n.selected_text, n.note_text, n.user_tags_json, n.bbox_json,
               n.matched_document_id, n.matched_chunk_id, n.matched_chunk_ids_json,
               n.review_status, n.mechanism_status, n.evidence_alignment_status,
               n.alignment_confidence, n.alignment_method,
               k.heading_path AS chunk_heading_path,
               k.chunk_text AS chunk_evidence_text,
               d.title AS document_title
        FROM zotero_inspiration_notes n
        LEFT JOIN knowledge_chunks k ON k.id = n.matched_chunk_id
        LEFT JOIN documents d ON d.id = n.matched_document_id
        WHERE {' AND '.join(predicates)}
        ORDER BY n.matched_document_id, n.pdf_page, n.id
        LIMIT ?
        """,
        (*params, max(1, limit)),
    ).fetchall()
    results = []
    for row in rows:
        item = _zotero_note_item(dict(row), terms=terms)
        item["match_reason"] = "note_neighbor"
        item["context_reason"] = "note_neighbor"
        results.append(item)
    return results


def _object_neighbor_items(
    conn: Any,
    evidence_items: list[dict[str, Any]],
    *,
    terms: list[str],
    document_id: int | None,
    chapter_id: int | None,
    limit: int,
) -> list[dict[str, Any]]:
    if not evidence_items or not table_exists(conn, "object_candidates"):
        return []
    chunk_ids = set(_chunk_ids(evidence_items))
    if not chunk_ids:
        return []
    predicates: list[str] = ["1 = 1"]
    params: list[Any] = []
    if document_id is not None:
        predicates.append("o.document_id = ?")
        params.append(document_id)
    if chapter_id is not None and "chapter_id" in columns(conn, "object_candidates"):
        predicates.append("(o.chapter_id = ? OR o.chapter_id IS NULL)")
        params.append(chapter_id)
    selected = (
        "o.id, o.document_id, o.chapter_id, o.object_key, o.object_name, "
        "o.object_type, o.review_status, o.status, o.confidence, "
        "o.aliases_json, o.description, o.mapped_chunk_ids_json, "
        "o.evidence_refs_json, o.mechanism_tags_json, d.title AS document_title"
    )
    rows = conn.execute(
        f"""
        SELECT {selected}
        FROM object_candidates o
        LEFT JOIN documents d ON d.id = o.document_id
        WHERE {' AND '.join(predicates)}
        ORDER BY o.document_id, o.id
        LIMIT ?
        """,
        (*params, MAX_CANDIDATE_ROWS),
    ).fetchall()
    results = []
    for row in rows:
        row_dict = dict(row)
        object_chunk_ids = set(_json_list(row_dict.get("mapped_chunk_ids_json")))
        if not object_chunk_ids.intersection(chunk_ids):
            continue
        item = _object_candidate_item(conn, row_dict, terms=terms)
        item["match_reason"] = "object_neighbor"
        item["context_reason"] = "object_neighbor"
        results.append(item)
        if len(results) >= limit:
            break
    return results


def _chunk_ids(items: list[dict[str, Any]]) -> list[int]:
    ids: list[int] = []
    for item in items:
        for value in [item.get("chunk_id"), *(item.get("source_chunk_ids") or [])]:
            chunk_id = _first_int([value])
            if chunk_id is not None and chunk_id not in ids:
                ids.append(chunk_id)
    return ids


def _workspace_layers(
    *,
    results: dict[str, list[dict[str, Any]]],
    table_status: dict[str, bool],
    notes_in_scope: int,
    warnings: list[str],
) -> dict[str, Any]:
    notes_available = table_status["zotero_inspiration_notes"] and notes_in_scope != 0
    relation_ready = any(item.get("review_status") == "approved_human_review_read_only" for item in results["objects"])
    return {
        "passage_results_with_pdf_preview": {
            "status": "available" if table_status["knowledge_chunks"] else "unavailable",
            "reason": "read-only keyword search over knowledge_chunks" if table_status["knowledge_chunks"] else "knowledge_chunks table unavailable",
            "results": results["evidence_chunks"],
            "no_direct_match": table_status["knowledge_chunks"] and not results["evidence_chunks"],
        },
        "note_results_with_pdf_preview": {
            "status": "available" if notes_available else "unavailable",
            "reason": "read-only keyword search over zotero_inspiration_notes" if notes_available else "No Zotero notes in this scope",
            "results": results["zotero_notes"],
            "no_direct_match": notes_available and not results["zotero_notes"],
        },
        "object_results": {
            "status": "available" if table_status["object_candidates"] or results["objects"] else "unavailable",
            "reason": "existing object candidates only; no generation",
            "results": results["objects"],
            "no_direct_match": (table_status["object_candidates"] or results["objects"] is not None) and not results["objects"],
        },
        "relation_results": {
            "status": "planned" if relation_ready else "locked",
            "reason": "relation candidates stay dry-run; Phase7H not entered",
            "results": [],
            "dry_run_candidate_count": 73 if relation_ready else 0,
            "relation_generated": False,
        },
        "insight_or_mechanism_results": {
            "status": "available" if results["mechanisms"] else "planned",
            "reason": "existing mechanism draft candidates only; no generation",
            "results": results["mechanisms"],
            "no_direct_match": table_status["mechanism_draft_candidates"] and not results["mechanisms"],
        },
        "warnings": warnings,
    }


def _structured_result(
    query: str,
    results: dict[str, list[dict[str, Any]]],
    safety: dict[str, bool],
    expansion: dict[str, Any],
    scope_expansion: dict[str, Any],
) -> dict[str, Any]:
    relation_candidate_count = 73 if results["objects"] else 0
    return {
        "query": query,
        "expanded_query_preview": " | ".join(expansion.get("expanded_terms") or []),
        "expanded_terms": expansion.get("expanded_terms") or [],
        "expansion_rules": expansion.get("expansion_rules") or [],
        "context_window": expansion.get("context_window"),
        "neighbor_expansion": expansion.get("neighbor_expansion") or {},
        "scope_expansion": scope_expansion,
        "evidence_results": results["evidence_chunks"],
        "note_results": results["zotero_notes"],
        "inspiration_results": [],
        "object_results": results["objects"],
        "approved_object_candidates": [item for item in results["objects"] if item.get("review_status") == "approved_human_review_read_only"],
        "relation_dry_run_summary": {
            "status": "planned",
            "reason": "relation_candidates_readiness_only_phase7h_locked",
            "candidate_count": relation_candidate_count,
            "phase7h_status": "locked_not_entered",
            "relation_save_disabled": True,
            "relation_generated": False,
        },
        "mechanism_readiness_summary": {
            "status": "locked",
            "reason": "relations_not_reviewed_phase7h",
            "existing_draft_count": len(results["mechanisms"]),
            "mechanism_generated": False,
        },
        "safety_flags": safety,
    }


def _research_evidence_packet_payload(query: str, results: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    retrieval_results = [
        *[_retrieval_result_from_chunk(item) for item in results.get("evidence_chunks", [])],
        *[_retrieval_result_from_note(item) for item in results.get("zotero_notes", [])],
    ]
    retrieval_results = [item for item in retrieval_results if item.get("stable_id")]
    quality_summary = _quality_summary(retrieval_results)
    groups = _simple_retrieval_groups(retrieval_results)
    related_keywords = _related_keywords(query, retrieval_results, results.get("objects", []))
    evidence_packet = _build_evidence_packet_text(
        query,
        retrieval_results,
        quality_summary,
        related_keywords=related_keywords,
    )
    evidence_packet_json = _build_evidence_packet_json(
        query=query,
        retrieval_results=retrieval_results,
        quality_summary=quality_summary,
        groups=groups,
        related_keywords=related_keywords,
    )
    evidence_packet_markdown = _build_evidence_packet_markdown(evidence_packet_json)
    return {
        "stage": "ResearchEvidencePacket-A",
        "extension_stage": "ResearchEvidencePacket-B",
        "status": "ready",
        "query": query,
        "retrieval_results": retrieval_results,
        "groups": groups,
        "quality_summary": quality_summary,
        "related_keywords": related_keywords,
        "evidence_packet": evidence_packet,
        "evidence_packet_markdown": evidence_packet_markdown,
        "evidence_packet_json": evidence_packet_json,
        "packet_task_instructions": _packet_task_instructions(),
        **_safety_flags(),
    }


def _retrieval_result_from_chunk(item: dict[str, Any]) -> dict[str, Any]:
    chunk_id = item.get("chunk_id")
    document_id = item.get("document_id")
    page = item.get("page") or item.get("pdf_page")
    stable_id = item.get("stable_id") or _stable_chunk_id(document_id, page, chunk_id)
    heading_path = item.get("heading_path") or item.get("chapter_label") or ""
    title = item.get("document_title") or item.get("source_title") or item.get("title") or ""
    return {
        "stable_id": stable_id,
        "source_type": "pdf_chunk",
        "source_entity_id": chunk_id,
        "document_id": document_id,
        "title": title,
        "source": title,
        "document_title": title,
        "chapter_id": item.get("chapter_id"),
        "chapter": item.get("chapter_title") or "",
        "section": _heading_title(heading_path),
        "heading_path": heading_path,
        "page": page,
        "score": item.get("score") or 0,
        "snippet": item.get("snippet") or "",
        "content": item.get("text") or item.get("snippet") or "",
        "raw_text_ref": item.get("raw_text_ref") or f"chunk:{chunk_id}",
        "citation_token": item.get("citation_token") or f"doc:{document_id}|chunk:{chunk_id}|page:{page}",
        "raw_metadata": _raw_retrieval_metadata(item),
    }


def _retrieval_result_from_note(item: dict[str, Any]) -> dict[str, Any]:
    note_identity = _note_identity(item)
    stable_id = item.get("stable_id") or _stable_note_id(note_identity)
    document_id = item.get("document_id")
    page = item.get("page") or item.get("pdf_page")
    note_text = item.get("note_text") or ""
    selected_text = item.get("selected_text") or ""
    content = note_text or selected_text or item.get("snippet") or ""
    title = note_text[:80] or item.get("title") or "Zotero inspiration note"
    return {
        "stable_id": stable_id,
        "source_type": "zotero_note",
        "source_entity_id": note_identity,
        "title": title,
        "source": "Zotero inspiration note",
        "document_title": item.get("document_title") or item.get("source_title") or "",
        "linked_document_id": document_id,
        "document_id": document_id,
        "chapter_id": item.get("chapter_id"),
        "chapter": item.get("chapter_title") or "",
        "section": _heading_title(item.get("heading_path") or item.get("chapter_label") or ""),
        "heading_path": item.get("heading_path") or item.get("chapter_label") or "",
        "page": page,
        "score": item.get("score") or 0,
        "snippet": item.get("snippet") or content,
        "content": content,
        "selected_text": selected_text,
        "note_text": note_text,
        "tags": item.get("tags") or item.get("user_tags") or [],
        "raw_text_ref": item.get("raw_text_ref") or f"note:{note_identity}",
        "citation_token": item.get("citation_token") or f"note:{note_identity}",
        "raw_metadata": _raw_retrieval_metadata(item),
    }


def _raw_retrieval_metadata(item: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "id",
        "source_kind",
        "source_type",
        "retrieval_source_type",
        "source_trace",
        "source_target",
        "source_locator",
        "citation_tokens",
        "citation_label",
        "match_reason",
        "context_reason",
        "scope_reason",
        "review_status",
        "mechanism_status",
        "server_note_id",
        "client_note_id",
        "zotero_annotation_key",
        "chunk_id",
        "matched_chunk_ids",
    ]
    return {key: item.get(key) for key in keys if item.get(key) not in (None, "", [])}


def _quality_summary(retrieval_results: list[dict[str, Any]]) -> dict[str, Any]:
    pdf_count = sum(1 for item in retrieval_results if item.get("source_type") == "pdf_chunk")
    note_count = sum(1 for item in retrieval_results if item.get("source_type") == "zotero_note")
    documents = sorted(
        {
            int(document_id)
            for document_id in (item.get("document_id") for item in retrieval_results)
            if document_id is not None
        }
    )
    chapter_keys = {
        str(item.get("chapter_id") or item.get("heading_path") or item.get("section") or "").strip()
        for item in retrieval_results
        if str(item.get("chapter_id") or item.get("heading_path") or item.get("section") or "").strip()
    }
    scores = [_numeric_score(item.get("score")) for item in retrieval_results]
    risks: list[str] = []
    if len(documents) == 1 and len(retrieval_results) > 1:
        risks.append("results_concentrated_in_single_document")
    if note_count == 0:
        risks.append("missing_zotero_notes")
    if pdf_count == 0:
        risks.append("missing_pdf_chunks")
    return {
        "pdf_chunks": pdf_count,
        "zotero_notes": note_count,
        "user_notes": note_count,
        "documents": len(documents),
        "document_ids": documents,
        "chapters": len(chapter_keys),
        "selected_results": len(retrieval_results),
        "results_concentrated_in_single_document": len(documents) == 1 and len(retrieval_results) > 1,
        "missing_zotero_notes": note_count == 0,
        "missing_pdf_chunks": pdf_count == 0,
        "score_highest": max(scores) if scores else None,
        "score_lowest": min(scores) if scores else None,
        "risks": risks,
    }


def _simple_retrieval_groups(retrieval_results: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "by_source_type": _group_ids(retrieval_results, lambda item: str(item.get("source_type") or "unknown")),
        "by_document": _group_ids(
            retrieval_results,
            lambda item: str(item.get("document_id") or "document_unknown"),
        ),
        "by_heading_path": _group_ids(
            retrieval_results,
            lambda item: str(item.get("heading_path") or item.get("section") or "heading_unknown"),
        ),
        "by_tag": _tag_groups(retrieval_results),
    }


def _group_ids(
    retrieval_results: list[dict[str, Any]],
    key_fn: Callable[[dict[str, Any]], str],
) -> list[dict[str, Any]]:
    groups: dict[str, list[str]] = {}
    for item in retrieval_results:
        key = key_fn(item).strip() or "unknown"
        groups.setdefault(key, []).append(str(item.get("stable_id")))
    return [
        {"key": key, "count": len(stable_ids), "stable_ids": stable_ids}
        for key, stable_ids in sorted(groups.items(), key=lambda pair: (-len(pair[1]), pair[0]))
    ]


def _tag_groups(retrieval_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[str]] = {}
    for item in retrieval_results:
        for tag in item.get("tags") or []:
            key = str(tag or "").strip()
            if key:
                groups.setdefault(key, []).append(str(item.get("stable_id")))
    return [
        {"key": key, "count": len(stable_ids), "stable_ids": stable_ids}
        for key, stable_ids in sorted(groups.items(), key=lambda pair: (-len(pair[1]), pair[0]))
    ]


def _build_evidence_packet_text(
    query: str,
    retrieval_results: list[dict[str, Any]],
    quality_summary: dict[str, Any],
    *,
    related_keywords: list[dict[str, Any]] | None = None,
) -> str:
    blocks = [
        "查询词：",
        query,
        "",
        "检索范围：",
        f"- PDF chunks: {quality_summary['pdf_chunks']}",
        f"- user notes: {quality_summary['zotero_notes']}",
        f"- documents: {quality_summary['documents']}",
        f"- selected results: {quality_summary['selected_results']}",
        "",
        "召回质量摘要：",
        f"- chapters: {quality_summary['chapters']}",
        f"- result concentrated in one document: {str(quality_summary['results_concentrated_in_single_document']).lower()}",
        f"- missing notes: {str(quality_summary['missing_zotero_notes']).lower()}",
        f"- missing PDF chunks: {str(quality_summary['missing_pdf_chunks']).lower()}",
        "",
        "证据包：",
        "",
    ]
    if related_keywords:
        blocks[-2:] = [
            "相关关键词：",
            *[f"- {item.get('keyword')}" for item in related_keywords if item.get("keyword")],
            "",
            "证据包：",
            "",
        ]
    for item in retrieval_results:
        blocks.extend(_packet_result_block(item))
    blocks.extend(["请完成：", *_packet_task_instructions()])
    return "\n".join(blocks).strip()


def _build_evidence_packet_json(
    *,
    query: str,
    retrieval_results: list[dict[str, Any]],
    quality_summary: dict[str, Any],
    groups: dict[str, Any],
    related_keywords: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "stage": "ResearchEvidencePacket-B",
        "base_stage": "ResearchEvidencePacket-A",
        "query": query,
        "retrieval_scope": {
            "pdf_chunks": quality_summary["pdf_chunks"],
            "user_notes": quality_summary["zotero_notes"],
            "documents": quality_summary["documents"],
            "selected_results": quality_summary["selected_results"],
        },
        "quality_summary": quality_summary,
        "groups": groups,
        "related_keywords": related_keywords,
        "results": [_packet_json_result(item) for item in retrieval_results],
        "task_instructions": _packet_task_instructions(),
        "safety_flags": _safety_flags(),
    }


def _packet_json_result(item: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "stable_id",
        "source_type",
        "source_entity_id",
        "title",
        "source",
        "document_title",
        "document_id",
        "linked_document_id",
        "chapter_id",
        "chapter",
        "section",
        "heading_path",
        "page",
        "score",
        "snippet",
        "content",
        "selected_text",
        "note_text",
        "tags",
        "raw_text_ref",
        "citation_token",
        "raw_metadata",
    ]
    packet_result = {key: item.get(key) for key in keys if item.get(key) not in (None, "", [])}
    packet_result["location"] = _result_location(item)
    return packet_result


def _build_evidence_packet_markdown(packet_json: dict[str, Any]) -> str:
    quality_summary = packet_json.get("quality_summary") or {}
    blocks = [
        "# Research Evidence Packet",
        "",
        "查询词：",
        str(packet_json.get("query") or ""),
        "",
        "## 检索范围",
        f"- PDF chunks: {quality_summary.get('pdf_chunks', 0)}",
        f"- user notes: {quality_summary.get('zotero_notes', quality_summary.get('user_notes', 0))}",
        f"- documents: {quality_summary.get('documents', 0)}",
        f"- selected results: {quality_summary.get('selected_results', 0)}",
        "",
        "## 召回质量摘要",
        f"- chapters: {quality_summary.get('chapters', 0)}",
        f"- result concentrated in one document: {str(quality_summary.get('results_concentrated_in_single_document', False)).lower()}",
        f"- missing notes: {str(quality_summary.get('missing_zotero_notes', False)).lower()}",
        f"- missing PDF chunks: {str(quality_summary.get('missing_pdf_chunks', False)).lower()}",
        "",
    ]
    related_keywords = packet_json.get("related_keywords") or []
    if related_keywords:
        blocks.extend([
            "## Related keywords",
            *[f"- {item.get('keyword')}" for item in related_keywords if item.get("keyword")],
            "",
        ])
    blocks.extend(["## 证据包", ""])
    for item in packet_json.get("results") or []:
        blocks.extend([
            f"### [{item.get('stable_id')}]",
            f"source_type: {item.get('source_type')}",
            f"source: {item.get('source') or item.get('document_title') or item.get('title') or ''}",
            f"location: {item.get('location') or 'location unavailable'}",
            f"score: {item.get('score')}",
            "content:",
            _compact_text(item.get("content") or item.get("snippet") or ""),
            "",
        ])
    blocks.extend(["## 请完成", *_packet_task_instructions()])
    return "\n".join(blocks).strip()


def _related_keywords(
    query: str,
    retrieval_results: list[dict[str, Any]],
    object_results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    query_terms = set(_keyword_terms(query))
    query_text = _normalize_related_keyword(query).lower()
    candidates: dict[str, dict[str, Any]] = {}

    def add_candidate(raw: Any, source: str, weight: float = 1.0) -> None:
        keyword = _normalize_related_keyword(raw)
        if not _related_keyword_allowed(keyword, query_text, query_terms):
            return
        key = keyword.lower()
        entry = candidates.setdefault(
            key,
            {
                "keyword": keyword,
                "score": 0.0,
                "source": source,
                "reason": "rule_result_terms",
            },
        )
        entry["score"] += weight
        if source not in str(entry["source"]).split(","):
            entry["source"] = f"{entry['source']},{source}"

    for item in retrieval_results:
        for tag in _as_text_list(item.get("tags")):
            add_candidate(tag, "tag", 3.0)
        for field, weight in (
            ("title", 2.0),
            ("heading_path", 2.0),
            ("section", 1.5),
            ("chapter", 1.5),
            ("snippet", 1.0),
            ("content", 0.5),
        ):
            for phrase in _related_keyword_candidates(item.get(field)):
                add_candidate(phrase, field, weight)

    for item in object_results:
        for field in ("object_name", "label", "title", "object_type", "description", "aliases_json", "mechanism_tags_json"):
            for phrase in _related_keyword_candidates(item.get(field)):
                add_candidate(phrase, f"object_{field}", 2.0)

    ranked = sorted(
        candidates.values(),
        key=lambda item: (-float(item["score"]), -len(str(item["keyword"]).split()), str(item["keyword"]).lower()),
    )
    return [
        {
            "keyword": item["keyword"],
            "score": round(float(item["score"]), 3),
            "source": item["source"],
            "reason": item["reason"],
        }
        for item in ranked[:MAX_RELATED_KEYWORDS]
    ]


def _related_keyword_candidates(value: Any) -> list[str]:
    text = _compact_text(value)
    if not text:
        return []
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9+\-]{2,}|[\u4e00-\u9fff]{2,8}", text)
    tokens = [token for token in tokens[:80] if token.lower() not in RELATED_KEYWORD_STOPWORDS]
    candidates: list[str] = []
    for size in (3, 2, 1):
        for index in range(0, max(0, len(tokens) - size + 1)):
            phrase = " ".join(tokens[index:index + size])
            if _related_keyword_allowed(phrase, "", set()):
                candidates.append(phrase)
    return candidates[:40]


def _keyword_terms(value: Any) -> list[str]:
    return [
        token.lower()
        for token in re.findall(r"[A-Za-z][A-Za-z0-9+\-]{2,}|[\u4e00-\u9fff]{2,8}", str(value or ""))
        if token.lower() not in RELATED_KEYWORD_STOPWORDS
    ]


def _normalize_related_keyword(value: Any) -> str:
    if isinstance(value, (dict, list, tuple)):
        value = json.dumps(value, ensure_ascii=False)
    text = _compact_text(value)
    text = re.sub(r"^[^\w\u4e00-\u9fff]+|[^\w\u4e00-\u9fff+\- ]+$", "", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def _related_keyword_allowed(keyword: str, query_text: str, query_terms: set[str]) -> bool:
    normalized = keyword.lower().strip()
    if len(normalized) < 3 or len(normalized) > 60:
        return False
    if normalized == query_text or normalized in query_terms:
        return False
    tokens = _keyword_terms(normalized)
    if not tokens:
        return False
    if len(tokens) == 1 and tokens[0] in RELATED_KEYWORD_STOPWORDS:
        return False
    return True


def _as_text_list(value: Any) -> list[str]:
    if value in (None, "", []):
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, tuple):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return [part.strip() for part in re.split(r"[,;|]", text) if part.strip()]
        return _as_text_list(parsed)
    return [str(value)]


def _packet_result_block(item: dict[str, Any]) -> list[str]:
    return [
        f"[{item.get('stable_id')}]",
        f"type: {item.get('source_type')}",
        f"source: {item.get('source') or item.get('document_title') or item.get('title') or ''}",
        f"location: {_result_location(item)}",
        f"score: {item.get('score')}",
        "content:",
        _compact_text(item.get("content") or item.get("snippet") or ""),
        "",
    ]


def _packet_task_instructions() -> list[str]:
    return [
        "1. 按主题簇整理这些材料；",
        "2. 区分 PDF 原文证据、用户笔记理解和模型推断；",
        "3. 总结概念联系；",
        "4. 提炼可迁移机制或研究启发；",
        "5. 指出还需要继续搜索的关键词；",
        "6. 每个关键结论后引用对应 ID；",
        "7. 不要编造没有证据支持的结论。",
    ]


def _result_location(item: dict[str, Any]) -> str:
    parts = []
    section = item.get("heading_path") or item.get("section") or item.get("chapter")
    if section:
        parts.append(str(section))
    if item.get("page"):
        parts.append(f"p.{item.get('page')}")
    if item.get("source_type") == "pdf_chunk" and item.get("source_entity_id"):
        parts.append(f"chunk {item.get('source_entity_id')}")
    return ", ".join(parts) if parts else "location unavailable"


def _chapter_scope(conn: Any, *, document_id: int | None, chapter_id: int | None) -> dict[str, Any]:
    if document_id is None or chapter_id is None or not table_exists(conn, "book_chapters"):
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


def _notes_in_scope_count(conn: Any, *, document_id: int | None, chapter: dict[str, Any]) -> int:
    if not table_exists(conn, "zotero_inspiration_notes"):
        return 0
    predicates: list[str] = []
    params: list[Any] = []
    if document_id is not None:
        predicates.append("matched_document_id = ?")
        params.append(document_id)
    if chapter:
        start = chapter.get("pdf_page_start")
        end = chapter.get("pdf_page_end") or start
        if start is not None and end is not None:
            predicates.append("pdf_page BETWEEN ? AND ?")
            params.extend([start, end])
    where = f"WHERE {' AND '.join(predicates)}" if predicates else ""
    row = conn.execute(f"SELECT COUNT(*) FROM zotero_inspiration_notes {where}", params).fetchone()
    return int(row[0]) if row else 0


def _chunk_by_id(conn: Any, chunk_id: int | None) -> dict[str, Any]:
    if chunk_id is None or not table_exists(conn, "knowledge_chunks"):
        return {}
    row = conn.execute(
        """
        SELECT k.id, k.document_id, k.chapter_id, k.heading_path, k.chunk_text,
               k.pdf_page_start, k.pdf_page_end, d.title AS document_title
        FROM knowledge_chunks k
        LEFT JOIN documents d ON d.id = k.document_id
        WHERE k.id = ?
        """,
        (chunk_id,),
    ).fetchone()
    return dict(row) if row else {}


def _source_target_from_chunk(
    source_kind: str,
    row: dict[str, Any],
    chunk: dict[str, Any],
    *,
    server_note_id: str = "",
) -> dict[str, Any] | None:
    if not chunk:
        return None
    return _source_target(
        source_kind=source_kind,
        row={**row, **chunk},
        page=chunk.get("pdf_page_start"),
        chunk_id=chunk.get("id"),
        chunk_evidence_text=chunk.get("chunk_text") or "",
        server_note_id=server_note_id,
    )


def _source_target(
    *,
    source_kind: str,
    row: dict[str, Any],
    page: int | None,
    chunk_id: int | None,
    selected_text: str = "",
    note_text: str = "",
    chunk_evidence_text: str = "",
    zotero_annotation_key: str = "",
    server_note_id: str = "",
    client_note_id: str = "",
    bbox: Any = None,
) -> dict[str, Any]:
    return {
        "sourceKind": source_kind,
        "documentId": row.get("document_id"),
        "chapterId": row.get("chapter_id"),
        "documentTitle": row.get("document_title") or "",
        "page": page,
        "pageLabel": f"p.{page}" if page else "",
        "selectedText": selected_text,
        "noteText": note_text,
        "chunkEvidenceText": chunk_evidence_text,
        "matchedChunkId": chunk_id,
        "chunkHeadingPath": row.get("heading_path") or row.get("chunk_heading_path") or "",
        "zoteroAnnotationKey": zotero_annotation_key,
        "serverNoteId": server_note_id,
        "clientNoteId": client_note_id,
        "objectCandidateId": (
            row.get("object_candidate_id")
            or (row.get("id") if source_kind == "object_evidence" else None)
        ),
        "objectCandidateIds": _source_target_object_ids(row, source_kind),
        "reviewedObjectRefs": _source_target_reviewed_object_refs(row, source_kind),
        "bbox": bbox,
        "alignmentStatus": row.get("evidence_alignment_status") or "",
        "alignmentConfidence": row.get("alignment_confidence") or "",
        "warnings": [],
        "developerMeta": {"source": "database_search_read_only"},
    }


def _source_target_object_ids(row: dict[str, Any], source_kind: str) -> list[int]:
    ids = _json_ints(row.get("matched_object_ids_json"))
    single = row.get("object_candidate_id")
    if single is None and source_kind == "object_evidence":
        single = row.get("id")
    try:
        object_id = int(single) if single is not None else None
    except (TypeError, ValueError):
        object_id = None
    if object_id is not None and object_id not in ids:
        ids.append(object_id)
    return ids
def _source_target_reviewed_object_refs(
    row: dict[str, Any],
    source_kind: str,
) -> list[str]:
    raw_refs = row.get("reviewed_object_refs")
    values = raw_refs if isinstance(raw_refs, list) else _json_list(raw_refs)
    candidate_ref = str(row.get("candidate_temp_id") or "").strip()
    if not candidate_ref and source_kind == "object_evidence":
        single = row.get("object_candidate_id")
        candidate_ref = str(single or "").strip()
        if candidate_ref.isdigit():
            candidate_ref = ""
    refs: list[str] = []
    for value in [*values, candidate_ref]:
        ref = str(value or "").strip()
        if ref and ref not in refs:
            refs.append(ref)
    return refs





def _snippet(text: str, terms: list[str]) -> str:
    normalized = _compact_text(text)
    if len(normalized) <= SNIPPET_CHARS:
        return normalized
    starts = [_term_first_index(normalized, term) for term in terms if _term_first_index(normalized, term) >= 0]
    start = max(0, min(starts) - 70) if starts else 0
    snippet = normalized[start : start + SNIPPET_CHARS].strip()
    if start > 0:
        snippet = "..." + snippet
    if start + SNIPPET_CHARS < len(normalized):
        snippet += "..."
    return snippet


def _heading_title(heading_path: str) -> str:
    parts = [part.strip() for part in str(heading_path or "").split("/") if part.strip()]
    return parts[-1] if parts else ""
