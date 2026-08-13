from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.external_api_config import DEFAULT_EXTERNAL_API_CONFIG, ExternalApiConfig
from app.db.session import SessionLocal
from app.models import (
    ChunkTag,
    Document,
    KnowledgeChunk,
    KnowledgeRelation,
    KnowledgeTag,
    NoteEvidenceLink,
    NoteTag,
    PersonalNote,
)
from app.services.keyword_search_service import build_pdf_open_url, search_keywords
from app.services.library_service import READ_LIBRARY_STATUSES
from app.services.rerank_service import RERANK_HEURISTIC, RerankOutcome, compute_heuristic_score, rerank_results
from app.services.search_helpers import load_chunk_tags, load_related_note_titles, make_snippet
from app.services.vector_index_service import (
    VectorIndexModelMismatchError,
    VectorIndexNotFoundError,
    vector_search,
)


DEFAULT_TOP_K = 10
FUSION_SNIPPET_CHARS = 160
VECTOR_ONLY_MIN_SCORE = 0.15
CHANNEL_KEYWORD = "keyword"
CHANNEL_VECTOR = "vector"
CHANNEL_TAG = "tag"
CHANNEL_RELATION = "relation"
CHANNEL_NOTE_LINK = "note_link"
DEFAULT_ENABLED_CHANNELS = {
    CHANNEL_KEYWORD,
    CHANNEL_VECTOR,
    CHANNEL_TAG,
    CHANNEL_RELATION,
    CHANNEL_NOTE_LINK,
}


@dataclass(frozen=True)
class FusedRetrievalResult:
    result_type: str
    id: int
    document_id: int
    chunk_id: int
    title: str
    document_title: str
    document_type: str
    heading_path: str
    snippet: str
    pdf_path: str | None
    pdf_page_start: int | None
    pdf_open_url: str | None
    zotero_open_url: str | None
    tags: list[str]
    related_notes: list[str]
    related_relations: list[int]
    matched_terms: list[str]
    tag_match_count: int
    related_note_count: int
    relation_count: int
    source_channels: list[str]
    fusion_score: float
    rerank_score: float
    keyword_score: float
    vector_score: float | None
    tag_score: float
    relation_score: float
    note_link_score: float


@dataclass(frozen=True)
class RetrievalFusionReport:
    query: str
    top_k: int
    rerank: str
    results: list[FusedRetrievalResult]
    external_rerank_called: bool
    external_call_audit: list[dict[str, Any]]
    degraded_reason: str | None
    local_degraded_reasons: list[str]


@dataclass
class _ChunkAccumulator:
    chunk_id: int
    source_channels: set[str]
    keyword_score: float = 0.0
    vector_score: float | None = None
    tag_score: float = 0.0
    relation_score: float = 0.0
    note_link_score: float = 0.0

    @property
    def fusion_score(self) -> float:
        vector = self.vector_score or 0.0
        source_bonus = len(self.source_channels) * 0.5
        return self.keyword_score + vector + self.tag_score + self.relation_score + self.note_link_score + source_bonus


def search_retrieval(
    query: str,
    top_k: int = DEFAULT_TOP_K,
    rerank: str = RERANK_HEURISTIC,
    external_api_config: ExternalApiConfig = DEFAULT_EXTERNAL_API_CONFIG,
    enabled_channels: set[str] | list[str] | tuple[str, ...] | None = None,
) -> RetrievalFusionReport:
    normalized_query = query.strip()
    if not normalized_query:
        raise ValueError("query must not be empty.")

    safe_top_k = max(1, top_k)
    pool_size = max(safe_top_k * 5, 20)
    accumulators: dict[int, _ChunkAccumulator] = {}
    local_degraded_reasons: list[str] = []
    active_channels = _normalize_enabled_channels(enabled_channels)

    if CHANNEL_KEYWORD in active_channels:
        _merge_keyword_results(accumulators, normalized_query, pool_size)
    if CHANNEL_VECTOR in active_channels:
        vector_reason = _merge_vector_results(accumulators, normalized_query, pool_size)
        if vector_reason:
            local_degraded_reasons.append(vector_reason)
    with SessionLocal() as session:
        if CHANNEL_TAG in active_channels:
            _merge_tag_results(session, accumulators, normalized_query, pool_size)
        if CHANNEL_RELATION in active_channels:
            _merge_relation_results(session, accumulators, normalized_query, pool_size)
        if CHANNEL_NOTE_LINK in active_channels:
            _merge_note_link_results(session, accumulators, normalized_query, pool_size)
        results = _hydrate_results(session, normalized_query, accumulators)

    results.sort(key=lambda result: result.fusion_score, reverse=True)
    rerank_outcome: RerankOutcome = rerank_results(
        query=normalized_query,
        results=results,
        mode=rerank,
        config=external_api_config,
    )
    reranked_results = _attach_rerank_scores(normalized_query, rerank_outcome.results, rerank)[:safe_top_k]
    return RetrievalFusionReport(
        query=normalized_query,
        top_k=safe_top_k,
        rerank=rerank,
        results=reranked_results,
        external_rerank_called=rerank_outcome.external_called,
        external_call_audit=rerank_outcome.audit_records if external_api_config.audit_external_calls else [],
        degraded_reason=rerank_outcome.degraded_reason,
        local_degraded_reasons=local_degraded_reasons,
    )


def _normalize_enabled_channels(enabled_channels: set[str] | list[str] | tuple[str, ...] | None) -> set[str]:
    if enabled_channels is None:
        return set(DEFAULT_ENABLED_CHANNELS)
    normalized = {channel.strip() for channel in enabled_channels if channel and channel.strip()}
    unknown = normalized - DEFAULT_ENABLED_CHANNELS
    if unknown:
        raise ValueError(f"unsupported retrieval channels: {', '.join(sorted(unknown))}")
    return normalized


def _attach_rerank_scores(
    query: str,
    results: list[FusedRetrievalResult],
    rerank: str,
) -> list[FusedRetrievalResult]:
    scored_results: list[FusedRetrievalResult] = []
    for result in results:
        score = compute_heuristic_score(query, result) if rerank == RERANK_HEURISTIC else result.fusion_score
        scored_results.append(replace(result, rerank_score=score))
    return scored_results


def _merge_keyword_results(accumulators: dict[int, _ChunkAccumulator], query: str, limit: int) -> None:
    seen_queries: set[str] = set()
    for candidate_query in _candidate_queries(query):
        if candidate_query in seen_queries:
            continue
        seen_queries.add(candidate_query)
        for status in sorted(READ_LIBRARY_STATUSES):
            for rank, result in enumerate(search_keywords(candidate_query, read_status=status, limit=limit), start=1):
                accumulator = _get_accumulator(accumulators, result.chunk_id)
                accumulator.source_channels.add("keyword")
                accumulator.keyword_score += max(0.2, 3.0 / rank)


def _merge_vector_results(accumulators: dict[int, _ChunkAccumulator], query: str, limit: int) -> str | None:
    try:
        for status in sorted(READ_LIBRARY_STATUSES):
            for result in vector_search(query, read_status=status, limit=limit):
                accumulator = _get_accumulator(accumulators, result.chunk_id)
                accumulator.source_channels.add("vector")
                accumulator.vector_score = max(accumulator.vector_score or 0.0, float(result.score))
    except (VectorIndexNotFoundError, VectorIndexModelMismatchError, RuntimeError, ValueError) as exc:
        return f"vector search skipped: {exc}"
    return None


def _merge_tag_results(
    session: Session,
    accumulators: dict[int, _ChunkAccumulator],
    query: str,
    limit: int,
) -> None:
    conditions = _tag_conditions(query)
    if not conditions:
        return

    chunk_rows = session.execute(
        select(ChunkTag.chunk_id)
        .join(KnowledgeTag, KnowledgeTag.id == ChunkTag.tag_id)
        .join(KnowledgeChunk, KnowledgeChunk.id == ChunkTag.chunk_id)
        .join(Document, Document.id == KnowledgeChunk.document_id)
        .where(Document.read_status.in_(READ_LIBRARY_STATUSES), or_(*conditions))
        .limit(limit)
    ).all()
    for (chunk_id,) in chunk_rows:
        accumulator = _get_accumulator(accumulators, chunk_id)
        accumulator.source_channels.add("tag")
        accumulator.tag_score += 1.5

    note_rows = session.execute(
        select(NoteEvidenceLink.chunk_id)
        .join(PersonalNote, PersonalNote.id == NoteEvidenceLink.note_id)
        .join(NoteTag, NoteTag.note_id == PersonalNote.id)
        .join(KnowledgeTag, KnowledgeTag.id == NoteTag.tag_id)
        .join(KnowledgeChunk, KnowledgeChunk.id == NoteEvidenceLink.chunk_id)
        .join(Document, Document.id == KnowledgeChunk.document_id)
        .where(Document.read_status.in_(READ_LIBRARY_STATUSES), or_(*conditions))
        .limit(limit)
    ).all()
    for (chunk_id,) in note_rows:
        accumulator = _get_accumulator(accumulators, chunk_id)
        accumulator.source_channels.add("tag")
        accumulator.tag_score += 1.0


def _merge_relation_results(
    session: Session,
    accumulators: dict[int, _ChunkAccumulator],
    query: str,
    limit: int,
) -> None:
    conditions = _relation_conditions(query)
    if not conditions:
        return
    rows = session.execute(
        select(KnowledgeRelation.evidence_chunk_id)
        .join(KnowledgeChunk, KnowledgeChunk.id == KnowledgeRelation.evidence_chunk_id)
        .join(Document, Document.id == KnowledgeChunk.document_id)
        .where(
            KnowledgeRelation.evidence_chunk_id.is_not(None),
            Document.read_status.in_(READ_LIBRARY_STATUSES),
            or_(*conditions),
        )
        .limit(limit)
    ).all()
    for (chunk_id,) in rows:
        if chunk_id is None:
            continue
        accumulator = _get_accumulator(accumulators, chunk_id)
        accumulator.source_channels.add("relation")
        accumulator.relation_score += 2.0


def _merge_note_link_results(
    session: Session,
    accumulators: dict[int, _ChunkAccumulator],
    query: str,
    limit: int,
) -> None:
    conditions = _note_conditions(query)
    if not conditions:
        return
    rows = session.execute(
        select(NoteEvidenceLink.chunk_id)
        .join(PersonalNote, PersonalNote.id == NoteEvidenceLink.note_id)
        .join(KnowledgeChunk, KnowledgeChunk.id == NoteEvidenceLink.chunk_id)
        .join(Document, Document.id == KnowledgeChunk.document_id)
        .where(Document.read_status.in_(READ_LIBRARY_STATUSES), or_(*conditions))
        .limit(limit)
    ).all()
    for (chunk_id,) in rows:
        accumulator = _get_accumulator(accumulators, chunk_id)
        accumulator.source_channels.add("note_link")
        accumulator.note_link_score += 2.0


def _hydrate_results(
    session: Session,
    query: str,
    accumulators: dict[int, _ChunkAccumulator],
) -> list[FusedRetrievalResult]:
    if not accumulators:
        return []
    chunk_ids = list(accumulators)
    rows = session.execute(
        select(Document, KnowledgeChunk)
        .join(KnowledgeChunk, KnowledgeChunk.document_id == Document.id)
        .where(KnowledgeChunk.id.in_(chunk_ids), Document.read_status.in_(READ_LIBRARY_STATUSES))
    ).all()
    chunk_tags = load_chunk_tags(session, [chunk.id for _, chunk in rows])
    related_note_titles = load_related_note_titles(session, [chunk.id for _, chunk in rows])
    relation_ids = _load_relation_ids_by_chunk(session, [chunk.id for _, chunk in rows])

    results: list[FusedRetrievalResult] = []
    for document, chunk in rows:
        accumulator = accumulators[chunk.id]
        if accumulator.source_channels == {"vector"} and (accumulator.vector_score or 0.0) < VECTOR_ONLY_MIN_SCORE:
            continue
        tags = chunk_tags.get(chunk.id, [])
        notes = related_note_titles.get(chunk.id, [])
        relations = relation_ids.get(chunk.id, [])
        matched_terms = _matched_terms(query, [document.title, chunk.heading_path, chunk.chunk_text, *tags])
        tag_match_count = len(_matched_terms(query, tags))
        results.append(
            FusedRetrievalResult(
                result_type="chunk_result",
                id=chunk.id,
                document_id=document.id,
                chunk_id=chunk.id,
                title=document.title,
                document_title=document.title,
                document_type=document.document_type,
                heading_path=chunk.heading_path,
                snippet=_cap_snippet(make_snippet(chunk.chunk_text, query, FUSION_SNIPPET_CHARS, match_tokens=True)),
                pdf_path=chunk.pdf_path or document.pdf_path,
                pdf_page_start=chunk.pdf_page_start,
                pdf_open_url=build_pdf_open_url(chunk.pdf_path or document.pdf_path, chunk.pdf_page_start),
                zotero_open_url=chunk.zotero_open_url,
                tags=tags,
                related_notes=notes,
                related_relations=relations,
                matched_terms=matched_terms,
                tag_match_count=tag_match_count,
                related_note_count=len(notes),
                relation_count=len(relations),
                source_channels=sorted(accumulator.source_channels),
                fusion_score=accumulator.fusion_score,
                rerank_score=accumulator.fusion_score,
                keyword_score=accumulator.keyword_score,
                vector_score=accumulator.vector_score,
                tag_score=accumulator.tag_score,
                relation_score=accumulator.relation_score,
                note_link_score=accumulator.note_link_score,
            )
        )
    return results


def _load_relation_ids_by_chunk(session: Session, chunk_ids: list[int]) -> dict[int, list[int]]:
    relation_ids: dict[int, list[int]] = {chunk_id: [] for chunk_id in chunk_ids}
    if not chunk_ids:
        return relation_ids
    rows = session.execute(
        select(KnowledgeRelation.evidence_chunk_id, KnowledgeRelation.id)
        .where(KnowledgeRelation.evidence_chunk_id.in_(chunk_ids))
        .order_by(KnowledgeRelation.id)
    ).all()
    for chunk_id, relation_id in rows:
        if chunk_id is not None:
            relation_ids.setdefault(chunk_id, []).append(relation_id)
    return relation_ids


def _matched_terms(query: str, text_parts: list[str]) -> list[str]:
    haystack = " ".join(str(part or "") for part in text_parts).lower()
    matched: list[str] = []
    for token in _query_tokens(query):
        lowered = token.lower()
        if lowered in haystack and lowered not in matched:
            matched.append(lowered)
        if len(matched) >= 20:
            break
    return matched


def _get_accumulator(accumulators: dict[int, _ChunkAccumulator], chunk_id: int) -> _ChunkAccumulator:
    if chunk_id not in accumulators:
        accumulators[chunk_id] = _ChunkAccumulator(chunk_id=chunk_id, source_channels=set())
    return accumulators[chunk_id]


def _candidate_queries(query: str) -> list[str]:
    candidates = [query]
    candidates.extend(_query_tokens(query))
    return [candidate for index, candidate in enumerate(candidates) if candidate and candidate not in candidates[:index]]


def _query_tokens(query: str) -> list[str]:
    raw_tokens = re.findall(r"[A-Za-z0-9_\-]+|[\u4e00-\u9fff]{2,}", query)
    tokens: list[str] = []
    for token in raw_tokens:
        cleaned = token.strip()
        if len(cleaned) < 2 or cleaned in tokens:
            continue
        tokens.append(cleaned)
        if re.fullmatch(r"[\u4e00-\u9fff]{5,}", cleaned):
            for ngram in _cjk_ngrams(cleaned):
                if ngram not in tokens:
                    tokens.append(ngram)
    return tokens


def _cjk_ngrams(text: str, max_tokens: int = 40) -> list[str]:
    ngrams: list[str] = []
    for size in (4, 3, 2):
        for start in range(0, max(0, len(text) - size + 1)):
            ngram = text[start : start + size]
            if ngram not in ngrams:
                ngrams.append(ngram)
            if len(ngrams) >= max_tokens:
                return ngrams
    return ngrams


def _tag_conditions(query: str) -> list[object]:
    conditions = []
    for candidate_query in _candidate_queries(query):
        like_query = f"%{_escape_like(candidate_query)}%"
        conditions.extend(
            [
                KnowledgeTag.name.like(like_query, escape="\\"),
                KnowledgeTag.description.like(like_query, escape="\\"),
            ]
        )
    return conditions


def _relation_conditions(query: str) -> list[object]:
    conditions = []
    for candidate_query in _candidate_queries(query):
        like_query = f"%{_escape_like(candidate_query)}%"
        conditions.extend(
            [
                KnowledgeRelation.relation_type.like(like_query, escape="\\"),
                KnowledgeRelation.description.like(like_query, escape="\\"),
            ]
        )
    return conditions


def _note_conditions(query: str) -> list[object]:
    conditions = []
    for candidate_query in _candidate_queries(query):
        like_query = f"%{_escape_like(candidate_query)}%"
        conditions.extend(
            [
                PersonalNote.title.like(like_query, escape="\\"),
                PersonalNote.summary.like(like_query, escape="\\"),
                PersonalNote.content.like(like_query, escape="\\"),
            ]
        )
    return conditions


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _cap_snippet(snippet: str) -> str:
    if len(snippet) <= FUSION_SNIPPET_CHARS:
        return snippet
    return snippet[: FUSION_SNIPPET_CHARS - 3].rstrip() + "..."
