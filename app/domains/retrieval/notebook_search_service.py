from __future__ import annotations

import time
import re
import sqlite3
import json
from typing import Any

from app.core.database import connect_readonly_sqlite
from app.core.paths import DEFAULT_DB_PATH
from app.domains.retrieval.fragment_repository import (
    cache_notebook_fragments,
    get_notebook_fragments,
)
from app.domains.retrieval.note_vector_index import (
    DEFAULT_RECALL_LIMIT,
    NoteVectorIndexUnavailable,
    search_zotero_note_vectors,
)
from app.domains.retrieval.result_contracts import (
    NOTE_SOURCE_TYPES,
    NotebookFragment,
    NotebookSearchResponse,
    PublicEvidence,
)
from app.domains.retrieval.public_evidence import serialize_public_evidence
from app.schemas.notebook_search import NotebookSearchRequest
from app.services import (
    high_quality_search_service,
    local_embedding_service,
    local_reranker_service,
)
from app.services.retrieval.fragment_id import canonical_source_locator, fragment_uuid


MODE = "high_quality_notebook_search_v1"


class NotebookSearchUnavailable(RuntimeError):
    pass


def search_notebook(
    request: NotebookSearchRequest | dict[str, Any],
) -> dict[str, Any]:
    search_request = (
        request
        if isinstance(request, NotebookSearchRequest)
        else NotebookSearchRequest.model_validate(request)
    )
    started = time.perf_counter()
    warnings: list[str | dict[str, Any]] = _requested_document_warnings(
        search_request.document_ids
    )
    candidates: list[dict[str, Any]] = []
    backends: list[str] = []

    if "pdf_chunk" in search_request.source_types:
        pdf_started = time.perf_counter()
        pdf_payload = high_quality_search_service.search_high_quality(
            search_request.query,
            include_objects=False,
        )
        backends.append(str(pdf_payload.get("retrieval_backend") or "legacy_high_quality"))
        fallback_reason = pdf_payload.get("fallback_reason")
        if fallback_reason:
            warnings.append(f"legacy_pdf_fallback:{fallback_reason}")
        candidates.extend(
            _pdf_candidates(
                pdf_payload,
                document_ids=set(search_request.document_ids),
            )
        )
        pdf_ms = _elapsed_ms(pdf_started)
    else:
        pdf_ms = 0.0

    requested_note_types = [
        source_type
        for source_type in search_request.source_types
        if source_type in NOTE_SOURCE_TYPES
    ]
    if requested_note_types:
        notes_started = time.perf_counter()
        try:
            note_payload = search_zotero_note_vectors(
                search_request.query,
                limit=DEFAULT_RECALL_LIMIT,
                source_types=requested_note_types,
                document_ids=search_request.document_ids,
            )
        except NoteVectorIndexUnavailable as exc:
            raise NotebookSearchUnavailable(str(exc)) from exc
        backends.append(str(note_payload.get("backend") or "zotero_note_vectors"))
        for raw_rank, item in enumerate(note_payload.get("results") or [], start=1):
            fragment = NotebookFragment.model_validate(item["fragment"])
            candidates.append(
                {
                    "kind": "note",
                    "fragment": fragment,
                    "passage_text": item["passage_text"],
                    "title": fragment.document_title or "",
                    "heading_path": "",
                    "semantic_score": float(item.get("semantic_score") or 0.0),
                    "raw_rank": raw_rank,
                }
            )
        notes_ms = _elapsed_ms(notes_started)
    else:
        notes_ms = 0.0

    if requested_note_types:
        ranked = _rerank_unified(search_request.query, candidates)
        ranked = _filter_relevant_notes_and_duplicates(search_request.query, ranked)
        if not any(item["kind"] == "note" for item in ranked):
            warnings.append("没有高相关用户笔记")
    else:
        ranked = candidates
    ranked, omitted_pdf_count = _filter_low_relevance_pdf_candidates(ranked)
    if omitted_pdf_count:
        warnings.append(
            f"low_relevance_pdf_candidates_omitted:{omitted_pdf_count}"
        )
    limited = ranked[: search_request.limit]
    cache_notebook_fragments(item["fragment"] for item in limited)
    results = [
        _result_from_candidate(
            item,
            final_rank=rank,
            include_context=search_request.include_context,
        )
        for rank, item in enumerate(limited, start=1)
    ]
    response = NotebookSearchResponse(
        query=search_request.query,
        embedding_model=local_embedding_service.MODEL_NAME,
        reranker_model=local_reranker_service.RERANKER_MODEL_NAME,
        backend="+".join(dict.fromkeys(backends)) or "high_quality_notebook_search",
        result_count=len(results),
        results=results,
        warnings=_dedupe_warnings(warnings),
        latency={
            "pdf_high_quality_ms": round(pdf_ms, 2),
            "note_vector_ms": round(notes_ms, 2),
            "total_ms": round(_elapsed_ms(started), 2),
        },
    )
    return response.model_dump(mode="json")


def _pdf_candidates(
    payload: dict[str, Any],
    *,
    document_ids: set[int],
) -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []
    for paper in payload.get("papers") or []:
        document_id = _int_or_none(paper.get("document_id"))
        if document_id is None or (document_ids and document_id not in document_ids):
            continue
        for passage in paper.get("top_passages") or []:
            chunk_id = _int_or_none(passage.get("chunk_id"))
            if chunk_id is None:
                continue
            flattened.append(
                {
                    "kind": "pdf",
                    "fragment_id": fragment_uuid(
                        canonical_source_locator(
                            "pdf_chunk", document_id=document_id, chunk_id=chunk_id
                        )
                    ),
                    "document_id": document_id,
                    "document_title": str(paper.get("title") or ""),
                    "document_type": paper.get("document_type"),
                    "chunk_id": chunk_id,
                    "pdf_page": _int_or_none(
                        passage.get("pdf_page") or passage.get("pdf_page_start")
                    ),
                    "page_label": None,
                    "passage_text": str(passage.get("passage_text") or ""),
                    "title": str(paper.get("title") or ""),
                    "heading_path": str(passage.get("heading_path") or ""),
                    "semantic_score": float(passage.get("embedding_score") or 0.0),
                    "reranker_score": float(passage.get("rerank_score") or 0.0),
                    "raw_rank": len(flattened) + 1,
                    "legacy_source_trace": passage.get("source_trace") or {},
                }
            )
    if not flattened:
        return []
    detail_document_ids = {
        int(item["document_id"])
        for item in flattened
    }
    details = {
        fragment.fragment_id: fragment
        for fragment in get_notebook_fragments(
            (item["fragment_id"] for item in flattened),
            document_ids=detail_document_ids,
        )
    }
    for candidate in flattened:
        candidate["fragment"] = details[candidate["fragment_id"]]
    return flattened


def _rerank_unified(query: str, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not candidates:
        return []
    timings: dict[str, float] = {}
    reranker = local_reranker_service._load_reranker(timings)  # noqa: SLF001
    pairs = [
        (
            query,
            local_reranker_service._candidate_text(  # noqa: SLF001
                {
                    "title": item.get("title") or "",
                    "heading_path": item.get("heading_path") or "",
                    "passage_text": item.get("passage_text") or "",
                }
            ),
        )
        for item in candidates
    ]
    scores = local_reranker_service._predict_scores(reranker, pairs)  # noqa: SLF001
    reranked = []
    for ordinal, (candidate, score) in enumerate(zip(candidates, scores)):
        reranked.append({**candidate, "reranker_score": float(score), "_ordinal": ordinal})
    reranked.sort(
        key=lambda item: (-float(item["reranker_score"]), int(item["_ordinal"]))
    )
    return reranked


def _result_from_candidate(
    candidate: dict[str, Any],
    *,
    final_rank: int,
    include_context: bool,
) -> PublicEvidence:
    fragment: NotebookFragment = candidate["fragment"]
    if candidate["kind"] == "pdf":
        fragment = fragment.model_copy(
            update={
                "document_title": candidate.get("document_title") or fragment.document_title,
                "document_type": candidate.get("document_type") or fragment.document_type,
                "chunk_id": candidate.get("chunk_id") or fragment.chunk_id,
                "pdf_page": candidate.get("pdf_page") or fragment.pdf_page,
                "heading": candidate.get("heading_path") or fragment.heading,
                "text": candidate.get("passage_text") or fragment.text,
            }
        )
    return serialize_public_evidence(
        fragment,
        selection_rank=final_rank,
        include_context=include_context,
    )


def _filter_relevant_notes_and_duplicates(
    query: str,
    ranked: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen_note_identities: set[tuple[Any, ...]] = set()
    for candidate in ranked:
        if candidate["kind"] != "note":
            result.append(candidate)
            continue
        if not _note_is_relevant(query, candidate):
            continue
        identity = _note_identity(candidate["fragment"])
        if identity in seen_note_identities:
            continue
        seen_note_identities.add(identity)
        result.append(candidate)
    return result


def _filter_low_relevance_pdf_candidates(
    ranked: list[dict[str, Any]],
    *,
    strong_score_floor: float = 4.0,
    maximum_score_gap: float = 1.25,
) -> tuple[list[dict[str, Any]], int]:
    pdf_scores = [
        float(item.get("reranker_score") or 0.0)
        for item in ranked
        if item.get("kind") == "pdf"
    ]
    if not pdf_scores:
        return ranked, 0
    top_score = max(pdf_scores)
    if top_score < strong_score_floor:
        return ranked, 0
    cutoff = top_score - maximum_score_gap
    filtered = [
        item
        for item in ranked
        if item.get("kind") != "pdf"
        or float(item.get("reranker_score") or 0.0) >= cutoff
    ]
    return filtered, len(ranked) - len(filtered)


def _note_is_relevant(query: str, candidate: dict[str, Any]) -> bool:
    reranker_score = float(candidate.get("reranker_score") or 0.0)
    semantic_score = float(candidate.get("semantic_score") or 0.0)
    if reranker_score < 0.0:
        return False
    fragment: NotebookFragment = candidate["fragment"]
    evidence = " ".join(
        value
        for value in (fragment.note_text, fragment.selected_text)
        if value
    )
    coverage = len(_concept_terms(query).intersection(_concept_terms(evidence)))
    return coverage >= 2 or (reranker_score >= 0.5 and semantic_score >= 0.65)


def _note_identity(fragment: NotebookFragment) -> tuple[Any, ...]:
    text = " ".join(
        value.strip().casefold()
        for value in (fragment.note_text, fragment.selected_text)
        if value and value.strip()
    )
    if fragment.zotero_annotation_key:
        return fragment.document_id, fragment.zotero_annotation_key, text
    return fragment.document_id, fragment.content_hash, text


def _concept_terms(value: str) -> set[str]:
    normalized = value.casefold()
    english = {
        token
        for token in re.findall(r"[a-z0-9]+", normalized)
        if len(token) >= 3
    }
    chinese_runs = re.findall(r"[\u3400-\u9fff]+", normalized)
    chinese = {
        run[index : index + 2]
        for run in chinese_runs
        for index in range(max(0, len(run) - 1))
    }
    return english | chinese


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _requested_document_warnings(
    document_ids: list[int],
) -> list[dict[str, Any]]:
    requested = sorted({int(value) for value in document_ids})
    if not requested:
        return []
    placeholders = ",".join("?" for _ in requested)
    try:
        with connect_readonly_sqlite(
            DEFAULT_DB_PATH,
            resolve_strict=True,
            query_only=True,
            temp_store="MEMORY",
        ) as connection:
            rows = connection.execute(
                f"""
                SELECT id, COALESCE(read_status, '') AS read_status
                  FROM documents
                 WHERE id IN ({placeholders})
                """,
                requested,
            ).fetchall()
    except (OSError, sqlite3.Error):
        return [
            {
                "code": "requested_document_validation_unavailable",
                "document_ids": requested,
            }
        ]
    statuses = {int(row[0]): str(row[1] or "") for row in rows}
    warnings: list[dict[str, Any]] = []
    missing = [value for value in requested if value not in statuses]
    archived = [
        value for value in requested if statuses.get(value) == "archived"
    ]
    if missing:
        warnings.append(
            {
                "code": "requested_document_not_found",
                "document_ids": missing,
            }
        )
    if archived:
        warnings.append(
            {
                "code": "requested_document_archived",
                "document_ids": archived,
            }
        )
    return warnings


def _dedupe_warnings(
    warnings: list[str | dict[str, Any]],
) -> list[str | dict[str, Any]]:
    result: list[str | dict[str, Any]] = []
    seen: set[str] = set()
    for warning in warnings:
        identity = (
            warning
            if isinstance(warning, str)
            else json.dumps(warning, sort_keys=True, ensure_ascii=True)
        )
        if identity in seen:
            continue
        seen.add(identity)
        result.append(warning)
    return result
