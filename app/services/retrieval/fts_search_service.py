from __future__ import annotations

import json
import sqlite3
import time
from collections import Counter
from contextlib import closing
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.schemas.retrieval_search import (
    RetrievalSearchRequest,
    RetrievalSearchResponse,
)
from app.services.retrieval.coverage_diversifier import diversify_coverage_results
from app.services.retrieval.duplicate_collapse import apply_duplicate_policy
from app.services.retrieval.fts_query_builder import (
    build_filter_clause,
    build_trigram_expression,
    build_unicode_expression,
)
from app.services.retrieval.fts_schema import (
    ORDINARY_TABLE,
    TRIGRAM_FTS_TABLE,
    UNICODE_FTS_TABLE,
)
from app.services.retrieval.fts_status_service import (
    DEFAULT_INDEX_PATH,
    DEFAULT_MANIFEST_PATH,
    DEFAULT_QUERY_ALIASES_PATH,
    connect_readonly_index,
    get_index_status,
)
from app.services.retrieval.match_reason import evaluate_match_signals
from app.services.retrieval.query_aliases import expand_curated_aliases
from app.services.retrieval.query_normalizer import (
    compact_identifier,
    normalize_query,
)
from app.services.retrieval.ranking import (
    score_candidate,
    sort_scored_candidates,
)


DEFAULT_PRECISION_LIMIT = 20
DEFAULT_COVERAGE_LIMIT = 50
MAX_CANDIDATE_POOL = 2000
BM25_WEIGHTS = (6.0, 3.0, 2.0, 1.0, 2.0, 0.75)


class RetrievalIndexUnavailable(RuntimeError):
    def __init__(self, status: dict[str, Any]) -> None:
        super().__init__(f"retrieval index is not ready: {status.get('status')}")
        self.status = status


def search_retrieval(
    request: RetrievalSearchRequest | dict[str, Any],
    *,
    index_path: str | Path | None = None,
    manifest_path: str | Path | None = None,
    query_aliases_path: str | Path = DEFAULT_QUERY_ALIASES_PATH,
) -> dict[str, Any]:
    started = time.perf_counter()
    search_request = (
        request
        if isinstance(request, RetrievalSearchRequest)
        else RetrievalSearchRequest.model_validate(request)
    )
    if index_path is None and manifest_path is None:
        from app.services.retrieval_generation_service import (
            current_retrieval_generation,
        )

        generation = current_retrieval_generation()
        index = generation.fts_index_path
        manifest = generation.fts_manifest_path
    else:
        index = Path(index_path) if index_path is not None else DEFAULT_INDEX_PATH
        manifest = Path(manifest_path) if manifest_path is not None else DEFAULT_MANIFEST_PATH
    aliases_path = Path(query_aliases_path)
    status = _cached_index_status(
        _status_signature(index, manifest, aliases_path),
        str(index.resolve(strict=False)),
        str(manifest.resolve(strict=False)),
        str(aliases_path.resolve(strict=False)),
    )
    if status.get("status") != "ready":
        raise RetrievalIndexUnavailable(status)

    plan = normalize_query(search_request.query)
    alias_matches = (
        expand_curated_aliases(plan.normalized_query, aliases_path=aliases_path)
        if search_request.mode == "coverage"
        else []
    )
    alias_terms = list(
        dict.fromkeys(
            term
            for match in alias_matches
            for term in match.expanded_terms
        )
    )
    unicode_expression = build_unicode_expression(
        plan,
        mode=search_request.mode,
        alias_terms=alias_terms,
    )
    trigram_expression = build_trigram_expression(
        plan,
        mode=search_request.mode,
    )
    effective_limit = search_request.limit or (
        DEFAULT_PRECISION_LIMIT
        if search_request.mode == "precision"
        else DEFAULT_COVERAGE_LIMIT
    )
    candidate_pool = min(
        MAX_CANDIDATE_POOL,
        max(200, (search_request.offset + effective_limit) * 20),
    )

    query_started = time.perf_counter()
    with closing(connect_readonly_index(index)) as connection:
        candidate_scores: dict[int, dict[str, Any]] = {}
        if unicode_expression:
            _collect_fts_candidates(
                connection,
                table=UNICODE_FTS_TABLE,
                expression=unicode_expression,
                filters=search_request.filters,
                limit=candidate_pool,
                channel="unicode61",
                target=candidate_scores,
            )
        if trigram_expression:
            _collect_fts_candidates(
                connection,
                table=TRIGRAM_FTS_TABLE,
                expression=trigram_expression,
                filters=search_request.filters,
                limit=candidate_pool,
                channel="trigram",
                target=candidate_scores,
            )
        if plan.short_query:
            _collect_short_query_candidates(
                connection,
                normalized_query=plan.normalized_query,
                contains_cjk=plan.contains_cjk,
                filters=search_request.filters,
                limit=candidate_pool,
                target=candidate_scores,
            )

        candidates = _load_candidate_rows(connection, candidate_scores)
        base_order = sorted(
            candidates,
            key=lambda item: (
                -float(item.get("_base_score") or 0.0),
                str(item.get("fragment_id") or ""),
            ),
        )
        for rank, candidate in enumerate(base_order, start=1):
            candidate["base_bm25_rank"] = rank

        scored = [
            score_candidate(
                candidate,
                evaluate_match_signals(
                    candidate,
                    plan=plan,
                    alias_terms=alias_terms,
                ),
            )
            for candidate in base_order
        ]
        scored = sort_scored_candidates(scored)
        scored, collapsed_count = apply_duplicate_policy(
            connection,
            scored,
            collapse_duplicates=search_request.collapse_duplicates,
        )
        scored = sort_scored_candidates(scored)
        if search_request.mode == "coverage":
            ranked, coverage_stats = diversify_coverage_results(scored)
        else:
            ranked = scored
            coverage_stats = _coverage_stats(scored)
        for final_rank, item in enumerate(ranked, start=1):
            item["final_rank"] = final_rank

    query_ms = round((time.perf_counter() - query_started) * 1000, 3)
    selected = ranked[
        search_request.offset : search_request.offset + effective_limit
    ]
    results = [
        _public_result(item, include_context=search_request.include_context)
        for item in selected
    ]
    response = {
        "status": "ok",
        "mode": search_request.mode,
        "query": search_request.query,
        "query_plan": {
            **plan.to_dict(),
            "curated_aliases": [item.to_dict() for item in alias_matches],
            "alias_expansion_enabled": search_request.mode == "coverage",
            "unicode_fts_expression": unicode_expression,
            "trigram_fts_expression": trigram_expression,
            "short_query_fallback": plan.short_query,
            "filters": search_request.filters.model_dump(mode="json"),
            "collapse_duplicates": search_request.collapse_duplicates,
            "include_context": search_request.include_context,
            "ranking_signals": [
                "BM25",
                "exact_phrase_match",
                "exact_identifier_match",
                "term_coverage",
                "title_match",
                "section_match",
                "tag_match",
                "curated_alias_match",
                "source_type_boost",
                "user_note_or_highlight_boost",
                *(
                    ["coverage_diversification"]
                    if search_request.mode == "coverage"
                    else []
                ),
            ],
        },
        "results": results,
        "counts": {
            "raw_candidates": len(candidates),
            "ranked_candidates": len(ranked),
            "duplicates_collapsed": collapsed_count,
            "returned": len(results),
            "offset": search_request.offset,
            "limit": effective_limit,
            "coverage": coverage_stats,
        },
        "timing_ms": {
            "query": query_ms,
            "total": round((time.perf_counter() - started) * 1000, 3),
        },
        "index_status": {
            "status": status["status"],
            "index_content_hash": status["index_content_hash"],
            "manifest_sha256": status["manifest_sha256"],
            "built_at": status["manifest"].get("built_at"),
        },
        "db_write_performed": False,
        "production_db_write_performed": False,
        "zotero_db_write_performed": False,
        "vector_write_performed": False,
        "llm_called": False,
    }
    return RetrievalSearchResponse.model_validate(response).model_dump(mode="json")


def _collect_fts_candidates(
    connection: sqlite3.Connection,
    *,
    table: str,
    expression: str,
    filters: Any,
    limit: int,
    channel: str,
    target: dict[int, dict[str, Any]],
) -> None:
    filter_sql, filter_parameters = build_filter_clause(filters)
    weights = ", ".join(str(value) for value in BM25_WEIGHTS)
    rows = connection.execute(
        f"""
        SELECT
            {table}.rowid AS row_id,
            -bm25({table}, {weights}) AS bm25_score
        FROM {table}
        JOIN {ORDINARY_TABLE} AS r ON r.row_id = {table}.rowid
        WHERE {table} MATCH ?
        {filter_sql}
        ORDER BY bm25({table}, {weights}), {table}.rowid
        LIMIT ?
        """,
        [expression, *filter_parameters, limit],
    ).fetchall()
    for row in rows:
        row_id = int(row["row_id"])
        score = max(float(row["bm25_score"] or 0.0), 0.0)
        entry = target.setdefault(row_id, {"scores": {}, "channels": set()})
        entry["scores"][channel] = max(score, entry["scores"].get(channel, 0.0))
        entry["channels"].add(channel)


def _collect_short_query_candidates(
    connection: sqlite3.Connection,
    *,
    normalized_query: str,
    contains_cjk: bool,
    filters: Any,
    limit: int,
    target: dict[int, dict[str, Any]],
) -> None:
    filter_sql, filter_parameters = build_filter_clause(filters)
    if contains_cjk:
        exact_sql = "instr(r.normalized_search_text, ?) > 0"
    else:
        exact_sql = "instr(' ' || r.normalized_search_text || ' ', ' ' || ? || ' ') > 0"
    rows = connection.execute(
        f"""
        SELECT r.row_id
        FROM {ORDINARY_TABLE} AS r
        WHERE {exact_sql}
        {filter_sql}
        ORDER BY r.row_id
        LIMIT ?
        """,
        [normalized_query, *filter_parameters, limit],
    ).fetchall()
    for row in rows:
        row_id = int(row["row_id"])
        entry = target.setdefault(row_id, {"scores": {}, "channels": set()})
        entry["scores"]["exact_fallback"] = max(
            0.25,
            entry["scores"].get("exact_fallback", 0.0),
        )
        entry["channels"].add("ordinary_exact_fallback")


def _load_candidate_rows(
    connection: sqlite3.Connection,
    score_map: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    if not score_map:
        return []
    row_ids = sorted(score_map)
    placeholders = ",".join("?" for _ in row_ids)
    rows = connection.execute(
        f"SELECT * FROM {ORDINARY_TABLE} WHERE row_id IN ({placeholders})",
        row_ids,
    ).fetchall()
    candidates = []
    for row in rows:
        data = dict(row)
        channel_data = score_map[int(data["row_id"])]
        scores = sorted(channel_data["scores"].values(), reverse=True)
        base_score = scores[0] + (0.1 * sum(scores[1:]) if len(scores) > 1 else 0.0)
        data.update(
            {
                "authors": _split_lines(data.get("authors_text")),
                "provenance": _json_list(data.get("provenance_json")),
                "warnings": [str(value) for value in _json_list(data.get("warnings_json"))],
                "duplicate_candidate": bool(data.get("duplicate_candidate")),
                "_base_score": base_score,
                "retrieval_channels": sorted(channel_data["channels"]),
            }
        )
        candidates.append(data)
    return candidates


def _public_result(
    item: dict[str, Any],
    *,
    include_context: bool,
) -> dict[str, Any]:
    return {
        "fragment_id": item["fragment_id"],
        "display_id": item["display_id"],
        "source_type": item["source_type"],
        "origin_kind": item["origin_kind"],
        "title": item.get("title"),
        "authors": item.get("authors") or [],
        "year": item.get("year"),
        "document_id": item.get("document_id"),
        "zotero_item_key": item.get("zotero_item_key"),
        "zotero_attachment_key": item.get("zotero_attachment_key"),
        "zotero_annotation_key": item.get("zotero_annotation_key"),
        "page_number": item.get("page_number"),
        "page_label": item.get("page_label"),
        "section": item.get("section"),
        "text": item["text"],
        "context_before": item.get("context_before") if include_context else None,
        "context_after": item.get("context_after") if include_context else None,
        "note_comment": item.get("note_comment"),
        "original_file_path": item.get("original_file_path"),
        "zotero_uri": item.get("zotero_uri"),
        "score": round(float(item.get("score") or 0.0), 6),
        "base_bm25_score": round(float(item.get("base_bm25_score") or 0.0), 6),
        "base_bm25_rank": int(item["base_bm25_rank"]),
        "final_rank": int(item["final_rank"]),
        "match_reasons": item.get("match_reasons") or [],
        "score_breakdown": {
            key: round(float(value), 6)
            for key, value in (item.get("score_breakdown") or {}).items()
        },
        "retrieval_channels": item.get("retrieval_channels") or [],
        "duplicate_count": int(item.get("duplicate_count") or 1),
        "duplicate_fragment_ids": item.get("duplicate_fragment_ids") or [item["fragment_id"]],
        "duplicate_source_types": item.get("duplicate_source_types") or [item["source_type"]],
        "provenance": item.get("provenance") or [],
        "warnings": item.get("warnings") or [],
    }


def _coverage_stats(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    documents = Counter(
        (
            f"document:{item['document_id']}"
            if item.get("document_id") is not None
            else f"attachment:{item.get('zotero_attachment_key') or 'none'}"
        )
        for item in candidates
    )
    source_types = Counter(str(item.get("source_type") or "") for item in candidates)
    return {
        "documents": len(documents),
        "source_types": len(source_types),
        "document_counts": dict(documents),
        "source_type_counts": dict(source_types),
    }


def _split_lines(value: object) -> list[str]:
    return [line for line in str(value or "").splitlines() if line]


def _json_list(value: object) -> list[Any]:
    try:
        parsed = json.loads(str(value or "[]"))
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def _status_signature(
    index_path: Path,
    manifest_path: Path,
    aliases_path: Path,
) -> tuple[tuple[str, int, int], ...]:
    from app.services.retrieval.fts_status_service import (
        DEFAULT_NOTES_ROOT,
        DEFAULT_ZOTERO_SNAPSHOT_PATH,
    )
    from app.core.paths import DEFAULT_DB_PATH

    paths = [
        index_path,
        manifest_path,
        aliases_path,
        DEFAULT_DB_PATH,
        DEFAULT_ZOTERO_SNAPSHOT_PATH,
        *(
            sorted(DEFAULT_NOTES_ROOT.rglob("*.md"))
            if DEFAULT_NOTES_ROOT.is_dir()
            else []
        ),
    ]
    signature = []
    for path in paths:
        resolved = path.resolve(strict=False)
        if resolved.is_file():
            stat = resolved.stat()
            signature.append((str(resolved), stat.st_size, stat.st_mtime_ns))
        else:
            signature.append((str(resolved), -1, -1))
    return tuple(signature)


@lru_cache(maxsize=8)
def _cached_index_status(
    signature: tuple[tuple[str, int, int], ...],
    index_path: str,
    manifest_path: str,
    aliases_path: str,
) -> dict[str, Any]:
    del signature
    return get_index_status(
        index_path=index_path,
        manifest_path=manifest_path,
        query_aliases_path=aliases_path,
    )
